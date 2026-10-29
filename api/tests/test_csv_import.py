"""CSV upload: header mapping, limits, dedupe, exclusions, owner scoping, enrichment of the uploaded rows."""

import csv
import io
import time
import uuid
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from app.agent.tools import registry
from app.agent.tools.handlers import enrich_email
from app.agent.tools.registry import ToolContext
from app.lists import csv_import
from app.lists.csv_import import CsvError, column_key, headers, parse_csv
from app.lists.rows import add_rows, to_csv
from app.main import app
from tests.conftest import act_in_own_org, member, project, sign_in, sign_out
from tests.test_agent import _user, needs_db
from tests.test_agent import db as db

client = TestClient(app)

PEOPLE_CSV = (
    '﻿First Name;Last Name;Company;Person Linkedin Url;Company Linkedin Url;CRM ID;Relevance score\n'
    'Anna;Neri;Acme;https://www.linkedin.com/in/anna/;https://linkedin.com/company/acme;42;99\n'
    'Anna;Neri;Acme;linkedin.com/in/anna;;;\n'  # same person: merged
    ';;;;;7;\n'  # no identity: skipped
    'Ex;Cluded;Banned;https://linkedin.com/in/ex;;;\n'
    ';;;;;;\n'  # blank: ignored
)


def test_maps_headers_and_keeps_extras():
    assert column_key('person', 'E-mail Address') == 'email'
    assert column_key('person', 'Città') == 'city'
    assert column_key('company', 'Company') == 'name'
    assert column_key('person', 'Company Linkedin Url') == 'company_linkedin_url'  # never the person's profile
    assert column_key('person', 'CRM ID') == 'crm_ref'  # not a vendor id enrichment would trust
    assert column_key('person', 'Relevance score') == ''
    rows, skipped = parse_csv(PEOPLE_CSV.encode(), 'person')
    assert skipped == 1 and len(rows) == 3
    assert rows[0] == {
        'first_name': 'Anna',
        'last_name': 'Neri',
        'company_name': 'Acme',
        'linkedin_url': 'https://www.linkedin.com/in/anna/',
        'company_linkedin_url': 'https://linkedin.com/company/acme',
        'crm_ref': '42',
    }
    # A user's Source column is data, not the vendor that wrote the row (cell_meta provider).
    assert parse_csv(b'domain,source\nacme.com,fiera 2025\n', 'company')[0] == [
        {'domain': 'acme.com', 'csv_source': 'fiera 2025'}
    ]


def test_headers_give_a_sample_per_column_and_count_non_blank_rows():
    heads, samples, rows = headers(PEOPLE_CSV.encode())
    assert heads[:3] == ['First Name', 'Last Name', 'Company'] and len(samples) == len(heads)
    assert samples[4:6] == ['https://linkedin.com/company/acme', '42'] and rows == 4


def test_reads_italian_excel_export():
    # Excel "CSV (delimitato dal separatore di elenco)": cp1252, ';', a quoted header holding a comma.
    raw = '"Nome, proprio";Cognome;Società;Città\nNiccolò;Rossi;Acme Srl;Forlì\n'.encode('cp1252')
    rows, _ = parse_csv(raw, 'person')
    assert rows == [{'nome_proprio': 'Niccolò', 'last_name': 'Rossi', 'company_name': 'Acme Srl', 'city': 'Forlì'}]


def test_rejects_unusable_files(monkeypatch):
    with pytest.raises(CsvError, match='known person field'):
        parse_csv(b'foo,bar\n1,2\n', 'person')
    with pytest.raises(CsvError, match='empty'):
        parse_csv(b'', 'company')
    monkeypatch.setattr(csv_import, 'MAX_ROWS', 2)
    with pytest.raises(CsvError, match='at most 2 rows'):
        parse_csv(b'domain\na.com\nb.com\nc.com\n' + b'x.com\n' * 100_000, 'company')
    # Only the first MAX_COLUMNS headers are mapped.
    calls = []
    monkeypatch.setattr(csv_import, 'column_key', lambda entity, h: calls.append(h) or 'domain')
    parse_csv(b','.join(b'h%d' % i for i in range(10_000)) + b'\na.com\n', 'company')
    assert len(calls) == csv_import.MAX_COLUMNS


def test_export_neutralises_formula_labels_and_cells():
    out = to_csv([{'key': '=cmd', 'label': '=cmd'}], [{'cells': {'=cmd': '@SUM(A1)'}, 'relevance_score': None}])
    assert out.splitlines() == ["'=cmd", "'@SUM(A1)"]


def test_export_neutralises_formulas_after_a_separator():
    # Excel in a ';' locale splits an unquoted value on ';': each segment starts a cell (#427)
    cells = ['CEO;=1+1;x', 'a\t=1+1', 'a;  +SUM(A1)', 'a\n-1', '@x']
    cols = [{'key': f'c{i}', 'label': f'c{i}'} for i in range(len(cells))] + [{'key': 'l', 'label': 'Title;=cmd'}]
    out = to_csv(cols, [{'cells': {f'c{i}': v for i, v in enumerate(cells)}, 'relevance_score': None}])
    for delimiter in (';', ','):
        fields = [f for row in csv.reader(io.StringIO(out), delimiter=delimiter) for f in row]
        assert fields and not [f for f in fields if f.lstrip()[:1] in ('=', '+', '-', '@')]
        assert not [f for row in fields for f in row.split('\t') if f.lstrip()[:1] in ('=', '+', '-', '@')]
    assert "CEO;'=1+1;x" in out and "a\t'=1+1" in out


def test_export_guard_is_linear_on_a_run_of_separators():
    started = time.monotonic()
    for run in ('\t' * 100_000, '\n' * 100_000, ' \t' * 50_000, ';"' * 50_000):
        to_csv([{'key': 'c', 'label': 'c'}], [{'cells': {'c': run + 'x'}, 'relevance_score': None}])
    assert time.monotonic() - started < 5


def test_export_neutralises_a_formula_behind_a_quote_in_every_column():
    cols = [{'key': 'a', 'label': 'a'}, {'key': 'b', 'label': 'b'}]
    out = to_csv(cols, [{'cells': {'a': 'Acme', 'b': 'x;"=1+1'}, 'relevance_score': None}])
    assert out.splitlines()[1] == 'Acme,"x;""\'=1+1"'


async def test_enrich_without_row_ids_takes_the_next_untried_rows(monkeypatch):
    seen: list = []
    taken: list = []
    monkeypatch.setattr(enrich_email, 'next_unenriched', lambda list_id: taken.append(list_id) or [f'{list_id}-r9'])
    monkeypatch.setattr(enrich_email, 'load_rows', lambda list_id, row_ids, *_: seen.append(row_ids) or [])
    ctx = ToolContext(user_id='u', list_id='l1')
    res = await enrich_email.handle({}, ctx)
    assert seen == [['l1-r9']] and res.ok and 'nothing_left' in res.data  # the batch loaded nobody to search (#562)
    # people without a row_id stay a free no-op: no fallback the user did not ask for (#140, #550)
    for people in ([{'name': 'Anna Neri'}], [{'first_name': 'Anna', 'company_name': 'Acme'}]):
        res = await enrich_email.handle({'people': people}, ctx)
        assert not res.ok and not res.row_updates and res.error_code == 'skipped'
    assert taken == ['l1'] and seen == [['l1-r9']]
    # the credit preflight covers the whole batch the fallback may take
    tool = registry.get('enrich_email')
    assert tool and tool.price and tool.price.quote({}, tool.input_schema).rows == enrich_email.MAX_PEOPLE
    assert tool.price.quote({'row_ids': ['a', 'b']}, tool.input_schema).rows == 2


def _imported(res) -> dict:
    assert res.status_code == 202, res.text
    done = client.get(f'/v1/lists/imports/{res.json()["id"]}').json()
    assert done['status'] == 'done', done
    return done


@needs_db
def test_vendor_merge_keeps_an_uploaded_phone(db, inline_worker):
    uid = _user(db)
    sign_in(app, member(db, uid))
    try:
        res = client.post(
            '/v1/lists/upload?entity=company', content=b'Company;Domain;Telefono\nAcme;acme.com;+39 02 111\n'
        )
        done = _imported(res)
    finally:
        sign_out(app)
    # google_maps owns `phone` (FIELD_OWNER) and would replace another vendor's number, never the user's.
    add_rows(db, done['list_id'], 'company', [{'domain': 'acme.com', 'phone': '+39 02 999', 'source': 'google_maps'}])
    row = db.execute('SELECT cells, cell_meta FROM list_rows WHERE list_id = %s', (done['list_id'],)).fetchone()
    owner = db.execute('SELECT user_id FROM lists WHERE id = %s', (done['list_id'],)).fetchone()['user_id']
    assert row['cells']['phone'] == '+39 02 111' and row['cell_meta']['phone'] == {'status': 'manual', 'by': str(owner)}


@needs_db
def test_upload_creates_scoped_list_and_feeds_enrichment(db, monkeypatch, inline_worker):
    uid, other = _user(db), _user(db)
    db.execute(
        "INSERT INTO exclusions (project_id, user_id, kind, value) VALUES (%s, %s, 'linkedin_url', 'linkedin.com/in/ex')",
        (project(db, uid), uid),
    )
    foreign_project = db.execute(
        "INSERT INTO projects (organization_id, user_id, name) VALUES (default_organization(%s), %s, 'theirs') RETURNING id",
        (
            other,
            other,
        ),
    ).fetchone()['id']
    sign_in(app, member(db, uid))
    try:
        url = '/v1/lists/upload?entity=person&name=Leads'
        res = client.post(url, content=PEOPLE_CSV.encode())
        body = _imported(res)
        assert (body['rows_added'], body['duplicates'], body['excluded'], body['skipped']) == (1, 1, 1, 1)
        assert client.post(f'{url}&project_id={foreign_project}', content=PEOPLE_CSV.encode()).status_code == 404
        sign_in(app, member(db, other))
        assert client.get(f'/v1/lists/{body["list_id"]}').status_code == 404
    finally:
        sign_out(app)

    lst = db.execute(
        'SELECT l.entity, l.mode, s.user_id FROM lists l JOIN chat_sessions s ON s.id = l.chat_session_id'
        ' WHERE l.id = %s',
        (body['list_id'],),
    ).fetchone()
    assert (lst['entity'], lst['mode']) == ('person', 'enrich') and str(lst['user_id']) == uid
    # A vendor row with the same identity fills gaps but never overwrites an uploaded value, even one it owns.
    monkeypatch.setattr('app.lists.rows.FIELD_OWNER', {'company_name': 'apollo'})
    add_rows(
        db,
        body['list_id'],
        'person',
        [{'linkedin_url': 'linkedin.com/in/anna', 'company_name': 'Vendor Co', 'title': 'CEO', 'source': 'apollo'}],
    )
    row = db.execute('SELECT cells, cell_meta FROM list_rows WHERE list_id = %s', (body['list_id'],)).fetchone()
    assert row['cells']['company_name'] == 'Acme' and row['cells']['title'] == 'CEO'
    owner = db.execute('SELECT user_id FROM lists WHERE id = %s', (body['list_id'],)).fetchone()['user_id']
    assert row['cell_meta']['company_name'] == {'status': 'manual', 'by': str(owner)}
    # The agent enriches it without row ids; a row already tried is not picked again.
    act_in_own_org(db, uid)  # the enrichment tool of uid's turn
    row_ids = enrich_email.next_unenriched(body['list_id'])
    assert len(row_ids) == 1
    # A rule added after the upload passes the row over instead of stalling the batch.
    db.execute(
        "INSERT INTO exclusions (project_id, user_id, kind, value) VALUES (%s, %s, 'company_name', 'acme')",
        (project(db, uid), uid),
    )
    assert enrich_email.next_unenriched(body['list_id']) == []
    db.execute("DELETE FROM exclusions WHERE user_id = %s AND kind = 'company_name'", (uid,))
    db.execute("""UPDATE list_rows SET cells = cells || '{"email_status": "not_found"}' WHERE id = %s""", (row_ids[0],))
    assert enrich_email.next_unenriched(body['list_id']) == []
    assert enrich_email.next_unenriched(str(uuid.uuid4())) == []


def test_mapping_overrides_the_detected_keys():
    raw = b'Contatto,Posta,Company,Note\nAda,ada@acme.it,Acme,vip\n'
    assert parse_csv(raw, 'person')[0] == [
        {'contatto': 'Ada', 'posta': 'ada@acme.it', 'company_name': 'Acme', 'note': 'vip'}
    ]
    # '' drops a column; `source` and unknown keys are not targets: the detected key stays.
    rows, _ = parse_csv(raw, 'person', ['full_name', 'email', '', 'source'])
    assert rows == [{'full_name': 'Ada', 'email': 'ada@acme.it', 'note': 'vip'}]
    with pytest.raises(CsvError, match='known person field'):
        parse_csv(raw, 'person', ['', 'notes', '', ''])


def test_xlsx_holds_the_csv_columns_as_plain_cells():
    import zipfile
    from xml.etree import ElementTree

    from app.lists.rows import to_xlsx

    cols = [{'key': k, 'label': k.title()} for k in ('relevance_score', 'name', 'source', 'person_id', 'note')]
    rows = [
        {
            'cells': {'name': '=HYPERLINK("x")', 'source': 'apollo', 'person_id': 'p1', 'note': 'a\x01b'},
            'relevance_score': 87,
        }
    ]
    with zipfile.ZipFile(io.BytesIO(to_xlsx(cols, rows))) as z:
        assert 'xl/workbook.xml' in z.namelist()
        sheet = ElementTree.fromstring(z.read('xl/worksheets/sheet1.xml'))  # noqa: S314 - our own output
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    table = [
        [c.attrib['r'] + '=' + ''.join(c.itertext()) for c in r.findall('m:c', ns)]
        for r in sheet.iter(f'{{{ns["m"]}}}row')
    ]
    assert table == [['A1=Relevance_Score', 'B1=Name', 'C1=Note'], ['A2=87', 'B2==HYPERLINK("x")', 'C2=ab']]
    # a formula-looking value is an inline string (never evaluated), the score a number
    assert sheet.find('.//m:c[@r="B2"]', ns).attrib['t'] == 'inlineStr'
    assert 't' not in sheet.find('.//m:c[@r="A2"]', ns).attrib


@needs_db
def test_preview_counts_match_the_import_and_export_honours_choices(db, inline_worker):
    uid, other = _user(db), _user(db)
    db.execute(
        "INSERT INTO exclusions (project_id, user_id, kind, value) VALUES (%s, %s, 'linkedin_url', 'linkedin.com/in/ex')",
        (project(db, uid), uid),
    )
    raw = PEOPLE_CSV.encode() + b'Bo;Bianchi;Beta;linkedin.com/in/bo;;;\n'
    sign_in(app, member(db, uid))
    try:
        preview = client.post('/v1/lists/upload/preview?entity=person', content=raw)
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body['headers'][:2] == ['First Name', 'Last Name'] and body['mapping'][:3] == [
            'first_name',
            'last_name',
            'company_name',
        ]
        assert body['rows'] == 5 and body['samples'][:3] == ['Anna', 'Neri', 'Acme']
        counts = {k: body[k] for k in ('to_import', 'duplicates', 'excluded', 'incomplete', 'skipped')}
        assert counts == {'to_import': 2, 'duplicates': 1, 'excluded': 1, 'incomplete': 2, 'skipped': 1}
        assert body['prices']['email'] == 1  # what each incomplete row's email costs, as enrich_email bills it
        assert db.execute('SELECT count(*) AS n FROM lists WHERE user_id = %s', (uid,)).fetchone()['n'] == 0
        bad = client.post('/v1/lists/upload/preview?entity=person&mapping=%7B%7D', content=raw)
        assert bad.status_code == 422 and bad.json()['detail']['code'] == 'LIST.CSV_BAD_MAPPING'
        huge = client.post(
            '/v1/lists/upload/preview', params={'entity': 'person', 'mapping': '[' + '"x",' * 3000 + '"x"]'}
        )
        assert huge.status_code == 422 and huge.json()['detail']['code'] == 'LIST.CSV_BAD_MAPPING'

        # the mapping reaches the import: Company becomes the email column here
        mapping = '["first_name","last_name","email","linkedin_url"]'
        res = _imported(client.post('/v1/lists/upload', params={'entity': 'person', 'mapping': mapping}, content=raw))
        assert (res['rows_added'], res['duplicates'], res['excluded']) == (2, 1, 1)
        lid = res['list_id']
        db.execute(
            "UPDATE list_rows SET relevance_score = 80 WHERE list_id = %s AND cells->>'first_name' = 'Bo'", (lid,)
        )

        xlsx = client.get(f'/v1/lists/{lid}/export.xlsx?rows=high_fit&columns=first_name&columns=email')
        assert xlsx.status_code == 200 and 'filename="' in xlsx.headers['content-disposition']
        assert xlsx.content[:2] == b'PK'
        # a name outside Latin-1 cannot go in a header as is: ASCII fallback plus filename* (RFC 6266)
        db.execute("UPDATE lists SET name = 'Lista Łódź 北京' WHERE id = %s", (lid,))
        for ext in ('csv', 'xlsx'):
            res = client.get(f'/v1/lists/{lid}/export.{ext}')
            assert res.status_code == 200, res.text
            encoded = res.headers['content-disposition'].split("filename*=UTF-8''")[1]
            assert unquote(encoded) == f'Lista-Łódź-北京.{ext}'
        csv_text = client.get(
            f'/v1/lists/{lid}/export.csv', params={'rows': 'high_fit', 'columns': ['first_name', 'email']}
        ).text
        assert csv_text.splitlines() == ['First name,Email', 'Bo,Beta']
        assert len(client.get(f'/v1/lists/{lid}/export.csv?rows=with_email').text.splitlines()) == 3
        # a rule added after the import keeps the row out of every export
        db.execute(
            "INSERT INTO exclusions (project_id, user_id, kind, value) VALUES (%s, %s, 'linkedin_url', 'linkedin.com/in/bo')",
            (project(db, uid), uid),
        )
        assert 'Bo' not in client.get(f'/v1/lists/{lid}/export.csv').text
        assert client.get(f'/v1/lists/{lid}/export.csv?rows=everything').status_code == 422
        company = client.post('/v1/lists/upload?entity=company', content=b'Company,Domain\nAcme,acme.com\n').json()
        no_email = client.get(f'/v1/lists/{company["list_id"]}/export.csv?rows=with_email')
        assert no_email.status_code == 422 and no_email.json()['detail']['code'] == 'LIST.EXPORT_INVALID'
        sign_in(app, member(db, other))
        assert client.get(f'/v1/lists/{lid}/export.xlsx').status_code == 404
    finally:
        sign_out(app)


@pytest.mark.parametrize(
    ('content', 'code'),
    [
        (b'', 'LIST.CSV_EMPTY'),
        (b'foo,bar\n1,2\n', 'LIST.CSV_NO_COLUMN'),
        (b'email\n' + b'a@b.co\n' * (csv_import.MAX_ROWS + 1), 'LIST.CSV_TOO_MANY_ROWS'),
    ],
)
def test_each_cause_of_an_unusable_file_has_its_own_code(content, code):
    with pytest.raises(CsvError) as e:
        parse_csv(content, 'person')
    assert e.value.code == code


def test_import_normalises_lookup_statuses_like_the_migration():
    content = (
        'First Name,Company,Email,Email Status,Phone,Phone Status\n'
        'A,Acme,a@x.com,Verified,1,Revealed\n'
        'B,Acme,," VALID ",,unavailable\n'
        'C,Acme,c@x.com,unavailable,3,guessed\n'
        'D,Acme,,unavailable,,\n'
        'E,Acme,e@x.com, INVALID ,,\n'
    )
    a, b, c, d, e = parse_csv(content.encode(), 'person')[0]
    assert (a['email_status'], a['phone_status']) == ('verified', 'revealed')
    assert (b['email_status'], b['phone_status']) == ('not_found', 'not_found')  # ' VALID ' is unknown, no address
    assert 'email_status' not in c and 'phone_status' not in c
    assert d['email_status'] == 'not_found' and 'phone_status' not in d
    assert e['email_status'] == 'invalid'
