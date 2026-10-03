from apps.shared.validation import Errors, read_number

from .constants import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE


def validate_list(query_params):
    errors = Errors()
    limit = read_number(errors, query_params, "limit", default=DEFAULT_PAGE_SIZE, minimum=1, maximum=MAX_PAGE_SIZE)
    offset = read_number(errors, query_params, "offset", default=0, minimum=0)
    errors.raise_if_any()

    return limit, offset
