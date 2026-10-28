"""Making a follow-up land in the thread it follows up on.

the earlier implementation's reference sequence labels its second email "same thread as the
first email (no new subject)". Ours could not express that at all: nothing
in ``app/outreach`` or ``app/integrations`` mentioned a thread, a
``Message-ID``, an ``In-Reply-To`` or a ``References``, so every follow-up
opened a fresh conversation with a fresh subject line. To the recipient that
reads as a second cold email rather than a nudge, and a mailbox that keeps
starting conversations nobody continues is a mailbox providers learn to
distrust.

## Why we mint the ``Message-ID`` instead of reading it back

Threading needs the identifier of the message being replied to, and neither
provider hands one back:

* Gmail answers a send with ``{"id": ..., "threadId": ...}``. Both are
  Gmail's own opaque ids, NOT the RFC 5322 ``Message-ID`` that a reply's
  ``In-Reply-To`` has to carry. Reading the real header back would need
  ``gmail.readonly`` — a *restricted* scope requiring an annual security
  assessment, which ``providers.py`` argues at length we must not ask for.
* Graph answers ``sendMail`` with ``202 Accepted`` and an empty body. There
  is nothing to read at all, and ``/messages/{id}/reply`` — the native reply
  verb — needs a message id we could only get with ``Mail.Read``.

So the identifier is ours: :func:`new_message_id` mints one before the send,
we stamp it on the outbound message through each provider's own mechanism,
and the ledger stores it. The follow-up quotes the stored value.

**The failure mode this design chooses.** A provider that overwrites our
``Message-ID`` with one of its own leaves the follow-up quoting an id that
no longer resolves. Mail clients then show it as a new conversation — which
is exactly today's behaviour, i.e. no worse than the status quo — and the
message is still delivered, correctly addressed, with its subject intact.
Threading degrades; it never breaks a send. That is the only direction this
was allowed to fail in, and it is why the ids are stamped rather than
demanded back.

## The seam

:class:`ThreadRef` is that seam. It carries what a reply needs in a form
neither provider is named in, and ``app.integrations.mailbox_sender`` turns it
into what each provider speaks:

* Gmail gets the RFC headers inside the raw MIME **and** ``threadId`` in the
  request body — its native handle, and the one that actually decides
  grouping in the Gmail UI.
* Graph gets the same headers in the raw MIME it accepts on ``sendMail``.
  It exposes no send-time thread handle: ``conversationId`` is read-only.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

#: How many prior ids a ``References`` header carries. RFC 5322 wants the
#: whole chain, and mail clients tolerate a trimmed one — but a campaign is
#: capped at ``MAX_STEPS`` (12) messages, so the chain is short by
#: construction and this bound only ever bites on malformed stored data.
MAX_REFERENCES = 12

#: Ceiling on one stored id, matching the column's CHECK. RFC 5322 puts no
#: hard limit on a msg-id; 500 is far past anything a real mailbox emits and
#: short enough that a corrupted value cannot bloat a header.
MAX_MESSAGE_ID = 500


@dataclass(frozen=True, slots=True)
class ThreadRef:
    """What a reply needs in order to land in an existing conversation.

    ``message_id`` is the id of the message being replied to — the direct
    parent, which becomes ``In-Reply-To``. ``references`` is the chain from
    the root down to and including that parent, which becomes ``References``.

    ``provider_thread_id`` is the provider's own handle when it has one and
    we learned it (Gmail's ``threadId``). Empty string, never ``None``, for
    the providers that have none: a caller that has to distinguish "no
    handle" from "handle we forgot to read" is a caller that will get it
    wrong, and there is no useful difference here — both mean "fall back to
    the headers".
    """

    message_id: str
    references: tuple[str, ...] = ()
    provider_thread_id: str = ''

    @property
    def header_references(self) -> str:
        """The ``References`` value, space-separated, oldest first.

        Falls back to the parent alone when no chain was stored — a reply
        with ``In-Reply-To`` and a one-entry ``References`` is well-formed,
        and it is what a two-message thread looks like anyway.
        """
        chain = self.references or (self.message_id,)
        return ' '.join(chain[-MAX_REFERENCES:])


def new_message_id(*, domain: str = '') -> str:
    """Mint an RFC 5322 ``msg-id`` for a message we are about to send.

    ``uuid4`` rather than a counter or a hash of the send: the id is quoted
    in a stranger's mailbox and must not encode who else we wrote to, when,
    or how many times. The domain part is cosmetic — no receiver resolves
    it — so an unconfigured deployment gets a stable literal rather than a
    header that is sometimes missing.
    """
    host = domain.strip() or 'zenbree.local'
    return f'<{uuid.uuid4()}@{host}>'


def normalise_message_id(raw: object) -> str:
    """A stored id as a header value, or ``''`` for anything unusable.

    Angle brackets are added when a writer stored the bare form, because a
    ``References`` value without them is not a msg-id and receivers drop the
    whole header rather than the one entry. Whitespace inside is fatal, not
    repairable: it would split one id into two and corrupt the chain.
    """
    if not isinstance(raw, str):
        return ''
    text = raw.strip()
    if not text or len(text) > MAX_MESSAGE_ID:
        return ''
    if not text.startswith('<'):
        text = f'<{text}'
    if not text.endswith('>'):
        text = f'{text}>'
    return '' if any(char.isspace() for char in text[1:-1]) else text


def reply_subject(root_subject: str) -> str:
    """The subject a threaded follow-up goes out under.

    Inherited from the message it replies to, never authored. That is the
    whole point of the feature — the reference calls it "no new subject" —
    and it is also what makes threading work at all in Gmail, which groups
    on the subject as well as the headers.

    ``Re:`` is added once and only once. A root subject that already carries
    one (because a human wrote it that way) is left as it is rather than
    growing ``Re: Re:`` with every step.
    """
    subject = root_subject.strip()
    if not subject:
        return ''
    return subject if subject[:3].lower() == 're:' else f'Re: {subject}'


@dataclass(frozen=True, slots=True)
class ProviderReceipt:
    """What a provider told us about a message it accepted.

    ``message_id`` is the provider's OWN id for the message (Gmail's
    ``id``), which is what the ledger already recorded as
    ``provider_receipt_id``. It is not the RFC ``Message-ID`` and must never
    be used as one — see this module's header.

    ``thread_id`` is the native thread handle when the provider returned
    one, which the next step passes straight back. Empty for Graph, which
    returns no body.
    """

    message_id: str = ''
    thread_id: str = ''


__all__ = [
    'MAX_MESSAGE_ID',
    'MAX_REFERENCES',
    'ProviderReceipt',
    'ThreadRef',
    'new_message_id',
    'normalise_message_id',
    'reply_subject',
]
