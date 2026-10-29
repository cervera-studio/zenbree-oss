import { describe, expect, it } from 'vite-plus/test';
import { parseExclusionsCsv } from './csv';

describe('parseExclusionsCsv', () => {
  it('takes a person by email, else LinkedIn, never by company', () => {
    const csv =
      'Full name,Company domain,Linkedin url,Email\r\n' +
      'Ada,acme.com,linkedin.com/in/ada,ada@acme.com\r\n' +
      'Bo,beta.it,linkedin.com/in/bo,\r\n' +
      'Ci,gamma.it,,\r\n';
    expect(parseExclusionsCsv(csv)).toEqual([
      { kind: 'email', value: 'ada@acme.com' },
      { kind: 'linkedin_url', value: 'linkedin.com/in/bo' },
    ]);
    expect(parseExclusionsCsv('First_name,E-mail\nAda,ada@acme.com')).toEqual([
      { kind: 'email', value: 'ada@acme.com' },
    ]);
  });

  it('takes a company by domain, else LinkedIn, else name', () => {
    const csv =
      'Name,Website,Linkedin url\nAcme,https://acme.com,\nBeta,,linkedin.com/company/beta\nGamma,,\n';
    expect(parseExclusionsCsv(csv)).toEqual([
      { kind: 'domain', value: 'https://acme.com' },
      { kind: 'linkedin_url', value: 'linkedin.com/company/beta' },
      { kind: 'company_name', value: 'Gamma' },
    ]);
  });

  it('reads quoted cells and semicolon files', () => {
    const csv = 'Name;Domain\n"Acme; Inc.";acme.com\n"Beta ""B""";\n';
    expect(parseExclusionsCsv(csv)).toEqual([
      { kind: 'domain', value: 'acme.com' },
      { kind: 'company_name', value: 'Beta "B"' },
    ]);
    expect(parseExclusionsCsv('Company name\n"Acme, Inc."\n"Beta ""B"""')).toEqual([
      { kind: 'company_name', value: 'Acme, Inc.' },
      { kind: 'company_name', value: 'Beta "B"' },
    ]);
  });

  it('takes the first column of a headerless list and leaves the kind to the API', () => {
    expect(parseExclusionsCsv('﻿acme.com\n\nada@beta.it, note\n')).toEqual([
      { value: 'acme.com' },
      { value: 'ada@beta.it' },
    ]);
  });
});
