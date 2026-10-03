"""Asking another account to send money.

An approval moves no money of its own. It claims the request, hands the request's own amount,
note and recipient to the transfer engine, and records the transfer that engine produced.
Every balance change, ledger entry and idempotency decision therefore belongs to the one
financial path R2 and R3 already established, and the only thing this feature knows about
money is which transfer paid which request.

MongoDB here is a standalone server, so the claim on the request and the transfer it pays
cannot be one transaction. They are arranged so that no interruption can leave a request
claiming a payment that did not happen, or a payment with nothing to show for it:

1. The request is claimed before the engine is called, so an approval and a decline arriving
   together cannot both proceed, and so nothing has been answered yet while money moves.
2. The claim is not a terminal state. A refusal from the engine hands the request back, so a
   request is never answered as paid and then found unpaid.
3. The key the engine spends is derived from the request, so however many approvals arrive at
   once, only one of them can create a transfer.
4. The transfer is recorded on the request in a single write, after the engine has reported
   the money moved, and only while the request is still in a state that may be paid.
"""

from datetime import timedelta, timezone

from apps.auth.repository import find_active_by_email, find_active_by_id, find_names_by_ids
from apps.shared.documents import now_utc
from apps.shared.errors import AppError, ValidationError
from apps.transfers import repository as transfer_repository
from apps.transfers import services as transfer_services
from apps.transfers.constants import COMPLETED_STATE, RECIPIENT_NOT_FOUND_MESSAGE, SELF_TRANSFER_MESSAGE
from apps.wallet import repository as wallet_repository
from apps.wallet.constants import WALLET_NOT_FOUND_MESSAGE

from . import repository
from .constants import (
    APPROVED_STATE,
    APPROVAL_CLAIM_GRACE_SECONDS,
    APPROVAL_KEY_PREFIX,
    APPROVER_ROLE,
    APPROVING_STATE,
    DECLINED_STATE,
    EXPIRED_STATE,
    PENDING_STATE,
    REQUEST_ACTION_NOT_ALLOWED_CODE,
    REQUEST_ACTION_NOT_ALLOWED_MESSAGE,
    REQUEST_ALREADY_APPROVED_CODE,
    REQUEST_ALREADY_APPROVED_MESSAGE,
    REQUEST_ALREADY_DECLINED_CODE,
    REQUEST_ALREADY_DECLINED_MESSAGE,
    REQUEST_APPROVAL_IN_PROGRESS_CODE,
    REQUEST_APPROVAL_IN_PROGRESS_MESSAGE,
    REQUEST_EXPIRED_CODE,
    REQUEST_EXPIRED_MESSAGE,
    REQUEST_NOT_FOUND_CODE,
    REQUEST_NOT_FOUND_MESSAGE,
    REQUEST_TTL_DAYS,
    REQUESTER_ROLE,
)

# What each state says to an action that could not take the request. Every one of them is a
# terminal answer except the last, which is the only thing that is true while another
# approval is still working on the request.
REFUSALS = {
    APPROVED_STATE: (REQUEST_ALREADY_APPROVED_CODE, REQUEST_ALREADY_APPROVED_MESSAGE),
    DECLINED_STATE: (REQUEST_ALREADY_DECLINED_CODE, REQUEST_ALREADY_DECLINED_MESSAGE),
    EXPIRED_STATE: (REQUEST_EXPIRED_CODE, REQUEST_EXPIRED_MESSAGE),
    APPROVING_STATE: (REQUEST_APPROVAL_IN_PROGRESS_CODE, REQUEST_APPROVAL_IN_PROGRESS_MESSAGE),
}


def approval_key(request_id):
    """The idempotency key an approval spends.

    Derived from the request rather than from the caller, so every attempt at approving this
    one request spends the same key and therefore names the same operation. R3's unique index
    then allows exactly one of them to create a transfer, however many arrive at once. The
    amount, note and recipient are all fixed when the request is made, so every attempt also
    produces the same fingerprint and none of them can be refused as a different request.
    The prefix is refused from an incoming `Idempotency-Key`, so this name cannot be spent on
    anything else.
    """
    return f"{APPROVAL_KEY_PREFIX}{request_id}"


def create_request(requester_account, approver_email, amount_minor, note):
    """Ask another account for money.

    Both parties are resolved before anything is written, and both are named by id, so the
    request holds an account rather than an address and cannot later point at somebody else's
    wallet. The window is the server's: a caller cannot ask for a request that stays open for
    ever, and cannot close one early either.
    """
    approver = resolve_approver(approver_email, requester_account)
    require_payable_wallet(requester_account)

    request = repository.create_request(
        {
            "requester_user_id": requester_account["_id"],
            "approver_user_id": approver["_id"],
            "amount_minor": amount_minor,
            "note": note or None,
            "state": PENDING_STATE,
            "expires_at": now_utc() + timedelta(days=REQUEST_TTL_DAYS),
        }
    )

    return public_request(request, requester_account["_id"])


def list_requests(account_id, role, state, limit, offset):
    """The account's own requests, newest first, with anything already paid recorded.

    Expiry is resolved before the page is read rather than by a job, so nothing shown is
    already over. A request whose approval has settled without the request finding out is
    recorded on the way out, which copies a result the engine produced rather than creating
    one, and costs one query for the whole page.
    """
    repository.expire_for_account(account_id, now_utc())
    requests = settle_finished(repository.list_for_account(account_id, role, state, limit, offset))
    names = names_for(requests, account_id)

    return {
        "requests": [public_request(request, account_id, names) for request in requests],
        "total": repository.count_for_account(account_id, role, state),
        "limit": limit,
        "offset": offset,
    }


def approve_request(approver_account, request_id):
    """Pay a request through the transfer engine, and record the transfer it produced.

    The claim comes first and is one conditional update, so of an approval, a decline and an
    expiry arriving together exactly one proceeds. The engine then decides whether the money
    can move: if it cannot, the claim is handed back and the request is answerable again; if
    it can, the transfer is recorded against the request and the approver is handed the
    engine's own answer, so the balance on screen is the balance that settled.
    """
    expire_for_approver(approver_account)
    request = load_request_for(approver_account, request_id)
    claim = claim_request(request, APPROVING_STATE)

    if claim is None:
        claim = take_over(request["_id"], approver_account)

    if claim["state"] == APPROVED_STATE:
        # This request has already been paid. Nothing is sent a second time; the answer is
        # the transfer that paid it.
        return paid_response(claim, approver_account)

    try:
        payload, created = transfer_services.execute_transfer(
            approver_account, payee_email(claim), claim["amountMinor"], claim.get("note"), approval_key(request["_id"])
        )
    except AppError:
        # The engine refuses before any money moves, so both wallets are exactly as they were
        # and the request can be answered once there is money to answer it with.
        repository.release_claim(request["_id"], now_utc())

        raise

    recorded = repository.settle_approved(request["_id"], payload["transfer"]["id"], now_utc())

    return approval_response(recorded or repository.find_request(request["_id"]), payload, created, approver_account)


def decline_request(approver_account, request_id):
    """Turn a request down.

    The same conditional claim as an approval, so a decline arriving while an approval is
    running matches nothing and is answered from the state the approval left behind. Declining
    moves no money, so once the claim is won there is nothing left to do but record it.
    """
    expire_for_approver(approver_account)
    request = load_request_for(approver_account, request_id)
    declined = claim_request(request, DECLINED_STATE)

    if declined is None:
        refuse(repository.find_request(request["_id"]))

    return public_request(declined, approver_account["_id"])


def resolve_approver(approver_email, requester_account):
    """The account being asked for money, found before anything is written.

    A request is only worth making if it can be answered, so somebody who cannot be paid is
    refused here, while nothing has happened yet. The rule and the words are the transfer
    route's own, because it is the same question being asked.
    """
    if approver_email == requester_account["email"]:
        raise ValidationError([], {"recipientEmail": [SELF_TRANSFER_MESSAGE]})

    account = find_active_by_email(approver_email)

    if account is None or wallet_repository.find_wallet_by_user_id(account["_id"]) is None:
        raise AppError(404, "RECIPIENT_NOT_FOUND", RECIPIENT_NOT_FOUND_MESSAGE)

    return account


def require_payable_wallet(requester_account):
    """Refuse to make a request from an account with no wallet to be paid into."""
    if wallet_repository.find_wallet_by_user_id(requester_account["_id"]) is None:
        raise AppError(404, "WALLET_NOT_FOUND", WALLET_NOT_FOUND_MESSAGE)


def expire_for_approver(account):
    """Bring this account's expired requests up to date before it acts on one of them."""
    repository.expire_for_account(account["_id"], now_utc())


def load_request_for(account, request_id):
    """The request, once it is known to exist and to be this account's to act on.

    A request belonging to another pair of accounts is refused here rather than answered, so
    no later step has to decide whether the caller was allowed to be holding it.
    """
    request = repository.find_request(request_id)

    if request is None:
        raise AppError(404, REQUEST_NOT_FOUND_CODE, REQUEST_NOT_FOUND_MESSAGE)

    if request["approverUserId"] != account["_id"]:
        raise AppError(403, REQUEST_ACTION_NOT_ALLOWED_CODE, REQUEST_ACTION_NOT_ALLOWED_MESSAGE)

    return request


def claim_request(request, claimed_state):
    """Take a request for one action, or say that it could not be taken.

    Expiry is settled first using the same instant the claim does, so a request that is over
    can never be taken and one that is not over can never be answered as though it were. A
    claim that matched nothing is tried once more, which covers the one case worth retrying:
    a request a competing approval released a moment ago and that is answerable again.
    """
    moment = now_utc()

    repository.expire_request(request["_id"], moment)

    claim = repository.claim(request["_id"], moment, claimed_state)

    if claim is not None:
        return claim

    if repository.find_request(request["_id"])["state"] != PENDING_STATE:
        return None

    return repository.claim(request["_id"], moment, claimed_state)


def take_over(request_id, account):
    """Decide what an approval should do when it could not claim the request.

    Somebody else's action got there first, so the state on the document is the answer. An
    approval that has already settled is recorded and handed back, because the request has
    been paid and saying so is more use than refusing. An approval that has not settled yet
    left a transfer behind that is either still running or not yet written; the first is
    reported, and the second is taken over only once it is old enough that nothing is working
    on it. Taking it over keeps the request claimed, so it cannot be declined in the meantime,
    and the derived key means a second attempt at the same approval cannot move money twice.
    """
    request = repository.find_request(request_id)

    if request["state"] == APPROVED_STATE:
        return request

    if request["state"] != APPROVING_STATE:
        refuse(request)

    transfer = transfer_repository.find_by_key(account["_id"], approval_key(request_id))

    if transfer is not None:
        if transfer["state"] != COMPLETED_STATE:
            refuse(request)

        return repository.settle_approved(request_id, transfer["_id"], now_utc())

    if not abandoned(request):
        refuse(request)

    return request


def refuse(request):
    """Answer an action that could not take a request, from the state it has reached."""
    code, message = REFUSALS[request["state"]]

    raise AppError(409, code, message)


def abandoned(request):
    """Whether a claim is old enough that nothing is working on it any more.

    Bounded exactly as transfers bound their recovery, and for the same reason: an approval
    claims the request and writes its transfer immediately, so a claim this old with no
    transfer behind it belongs to an approval that is gone. Taking it over cannot move money
    twice either way, because the key is what allows the attempt.
    """
    started = request.get("approvalStartedAt")

    if started is None:
        return False

    return as_instant(started) <= now_utc() - timedelta(seconds=APPROVAL_CLAIM_GRACE_SECONDS)


def as_instant(moment):
    """A moment read out of the database, comparable with the clock.

    A datetime read through the raw driver carries no offset, because BSON stores UTC
    milliseconds and nothing more, while one read through the document layer carries UTC
    explicitly. Both name the same instant, so the missing offset is filled in rather than
    taken off the value that has one.
    """
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def payee_email(request):
    """The address the engine should pay, resolved now rather than remembered.

    The request names the account, never an address, so an approval cannot pay an address
    that has since been changed to somebody else's. An account that is no longer payable is
    refused with the engine's own answer, and the claim is handed back with it.
    """
    account = find_active_by_id(str(request["requesterUserId"]))

    if account is None:
        raise AppError(404, "RECIPIENT_NOT_FOUND", RECIPIENT_NOT_FOUND_MESSAGE)

    return account["email"]


def settle_finished(requests):
    """Record approvals that have been paid without their request finding out.

    An approval writes its transfer before it records anything on the request, so an
    interruption in between leaves a request that has stopped agreeing with the money. This
    copies a result the engine has already produced and moves no money itself, so a request
    the engine has not finished is left exactly as it is, for the next approval attempt. The
    whole page is asked about in one query, the way a history page resolves the other party of
    every one of its entries in one query.
    """
    keys = {request["_id"]: approval_key(request["_id"]) for request in requests if request["state"] == APPROVING_STATE}

    if not keys:
        return requests

    approvers = {request["approverUserId"] for request in requests if request["_id"] in keys}
    transfers = transfer_repository.find_by_keys(approvers, list(keys.values()))

    for position, request in enumerate(requests):
        transfer = transfers.get(keys.get(request["_id"]))

        if transfer is not None and transfer["state"] == COMPLETED_STATE:
            recorded = repository.settle_approved(request["_id"], transfer["_id"], now_utc())

            if recorded is not None:
                requests[position] = recorded

    return requests


def approval_response(request, payload, created, account):
    """The request as it now stands, with the engine's own answer beside it."""
    return {"request": public_request(request, account["_id"]), **payload, "idempotentReplay": not created}


def paid_response(request, account):
    """What an approval of an already-approved request answers with.

    The transfer that paid it is the record of the money, so the answer is that transfer and
    the approver's own wallet as it stands now — the same answer a retried transfer gives,
    which is what makes a repeated approval read as the payment it repeated.
    """
    transfer = transfer_repository.find_transfer(request["transferId"])

    return {
        "request": public_request(request, account["_id"]),
        **transfer_services.transfer_response(account, transfer),
        "idempotentReplay": True,
    }


def other_party(request, viewer_account_id):
    return request["approverUserId"] if request["requesterUserId"] == viewer_account_id else request["requesterUserId"]


def role_for(request, viewer_account_id):
    """Which side of the request the viewer is on: the one asking, or the one being asked.

    Derived from the two ids rather than stored, so a request can never record a viewer as
    being on a side it is not.
    """
    return REQUESTER_ROLE if request["requesterUserId"] == viewer_account_id else APPROVER_ROLE


def names_for(requests, viewer_account_id):
    """The names of the other party on each request, so a page costs one extra query.

    The request holds no names: who a request is with is resolved when it is read, the same
    way the ledger resolves who a transfer was with, rather than copied onto the document
    when it is written.
    """
    if not requests:
        return {}

    return find_names_by_ids({other_party(request, viewer_account_id) for request in requests})


def public_request(request, viewer_account_id, names=None):
    """A request as the viewer sees it: which side they are on, and who the other side is.

    The two roles are complementary because a request has exactly two sides, so the role
    inside `counterparty` is the side the other account is on rather than a repeat of the
    viewer's own. A caller can therefore read the request without working out who is who.
    """
    names = find_names_by_ids({other_party(request, viewer_account_id)}) if names is None else names
    other = other_party(request, viewer_account_id)
    role = role_for(request, viewer_account_id)
    their_role = APPROVER_ROLE if role == REQUESTER_ROLE else REQUESTER_ROLE

    return {
        "id": str(request["_id"]),
        "state": request["state"],
        "role": role,
        "requesterUserId": str(request["requesterUserId"]),
        "approverUserId": str(request["approverUserId"]),
        "amountMinor": request["amountMinor"],
        "note": request.get("note"),
        "createdAt": request["createdAt"],
        "updatedAt": request["updatedAt"],
        "expiresAt": request["expiresAt"],
        "resolvedAt": request.get("resolvedAt"),
        "transferId": str(request["transferId"]) if request.get("transferId") else None,
        "counterparty": {"userId": str(other), "name": names.get(other, "Unknown account"), "role": their_role},
    }
