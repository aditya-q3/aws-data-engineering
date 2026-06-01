
import json
import traceback
import base64

from urllib.parse import urlencode

from common.logger import get_logger
from common.retry import retry_with_backoff
from common.http_client import (
    http_get,
    http_post
)

from common.secrets import (
    discover_client_secrets
)

from common.checkpoint import (
    get_watermark,
    update_watermark
)

from common.s3_writer import (
    write_json
)

from common.utils import (
    parse_timestamp,
    get_max_timestamp,
    build_s3_key
)

# =====================================================
# LOGGER
# =====================================================

logger = get_logger()

# =====================================================
# CONFIG
# =====================================================

BUCKET = "sentrias-phase-2"

SOURCE = "ninjaone"

# =====================================================
# ENDPOINTS
# =====================================================

ENDPOINT_MAP = {

    "alerts":
        "/v2/alerts",

    "activities":
        "/v2/activities",

    "devices":
        "/v2/devices-detailed",

    "organizations":
        "/v2/organizations-detailed",

    "ospatches":
        "/v2/queries/os-patches",

    "swpatches":
        "/v2/queries/software-patches"
}

TABLE_TIMESTAMP_FIELD = {

    "activities":
        "activityTime",

    "alerts":
        "updateTime",

    "devices":
        "lastUpdate",

    "organizations":
        "updatedAt",

    "ospatches":
        "timestamp",

    "swpatches":
        "timestamp"
}

INCREMENTAL_PARAM_MAP = {

    "alerts":
        "since",

    "activities":
        "since",

    "devices":
        "since",

    "organizations":
        "since",

    "ospatches":
        "since",

    "swpatches":
        "since"
}

TABLES = list(
    ENDPOINT_MAP.keys()
)

# =====================================================
# ACCESS TOKEN
# =====================================================

def get_access_token(
    client_secret
):

    auth_string = (

        f"{client_secret['client_id']}:"
        f"{client_secret['client_secret']}"
    )

    basic_auth = base64.b64encode(
        auth_string.encode()
    ).decode()

    payload = urlencode({

        "grant_type":
            "client_credentials",

        "scope":
            client_secret[
                "scope"
            ]
    })

    response = retry_with_backoff(

        lambda: http_post(

            client_secret[
                "oauth_url"
            ],

            body=payload,

            headers={

                "Authorization":
                    f"Basic {basic_auth}",

                "Content-Type":
                    "application/x-www-form-urlencoded"
            }
        )
    )

    if response.status != 200:

        raise Exception(
            f"Token failed "
            f"{response.status}"
        )

    token = json.loads(
        response.data.decode()
    ).get(
        "access_token"
    )

    if not token:

        raise Exception(
            "Missing token"
        )

    return token

# =====================================================
# APPLY INCREMENTAL
# =====================================================

def apply_incremental_filter(
    table,
    params,
    start_ts
):

    if start_ts is None:

        return params

    incremental_key = (
        INCREMENTAL_PARAM_MAP.get(
            table
        )
    )

    if not incremental_key:

        return params

    params[
        incremental_key
    ] = (
        float(start_ts)
        + 0.001
    )

    return params

# =====================================================
# FILTER NEW RECORDS
# =====================================================

def filter_new_records(
    rows,
    table,
    start_ts
):

    if start_ts is None:

        return rows

    ts_col = (
        TABLE_TIMESTAMP_FIELD.get(
            table
        )
    )

    if not ts_col:

        return rows

    filtered = []

    for row in rows:

        try:

            parsed_ts = parse_timestamp(
                row.get(ts_col)
            )

            if parsed_ts is None:

                filtered.append(
                    row
                )

                continue

            if parsed_ts > float(
                start_ts
            ):

                filtered.append(
                    row
                )

        except:

            filtered.append(
                row
            )

    return filtered

# =====================================================
# FETCH DATA
# =====================================================

def fetch_ninjaone_data(
    client_id,
    client_secret,
    table,
    start_ts
):

    token = get_access_token(
        client_secret
    )

    headers = {

        "Authorization":
            f"Bearer {token}",

        "Accept":
            "application/json"
    }

    org_id = client_secret.get(
        "organization_id"
    )

    if (
        table != "organizations"
        and
        not org_id
    ):

        logger.warning(
            f"{client_id} "
            f"{table} "
            f"org missing"
        )

        return []
    base_url = (
        client_secret.get(
            "base_url"
        )
    )

    if not base_url:

        raise Exception(
            f"{client_id} "
            f"missing base_url "
            f"in secret"
        )

    base_url = base_url.rstrip("/")

    url = (

        f"{base_url}/api"
        f"{ENDPOINT_MAP[table]}"
    )

    if table != "organizations":

        url += (
            f"?df=org={org_id}"
        )

    logger.info(
        f"{client_id} "
        f"{table} "
        f"url={url}"
    )

    all_rows = []

    next_cursor = None

    while True:

        if not next_cursor:

            params = {

                "pageSize":
                    1000
            }

            params = apply_incremental_filter(

                table,

                params,

                start_ts
            )

            logger.info(
                f"{client_id} "
                f"{table} "
                f"incremental={params}"
            )

        else:

            params = {

                "cursor":
                    next_cursor
            }

        separator = (
            "&"
            if "?" in url
            else "?"
        )

        request_url = (

            f"{url}"
            f"{separator}"
            f"{urlencode(params)}"
        )

        response = retry_with_backoff(

            lambda: http_get(

                request_url,

                headers=headers
            )
        )

        if response.status != 200:

            raise Exception(
                f"{table} "
                f"failed "
                f"{response.status}"
            )

        payload = json.loads(
            response.data.decode()
        )

        # if isinstance(
        #     payload,
        #     list
        # ):

        #     rows = payload

        #     next_cursor = None

        # else:

        #     rows = (

        #         payload.get(
        #             "items"
        #         )

        #         or payload.get(
        #             "data"
        #         )

        #         or payload.get(
        #             "results"
        #         )

        #         or payload.get(
        #             "activities"
        #         )

        #         or []
        #     )

        #     next_cursor = (

        #         payload.get(
        #             "nextCursor"
        #         )

        #         or payload.get(
        #             "next"
        #         )
        #     )

        if isinstance(
            payload,
            list
        ):

            rows = payload

            # ==========================================
            # ALERTS PAGINATION FIX
            # ==========================================
            next_cursor = (

                response.headers.get(
                    "X-Next-Cursor"
                )

                or response.headers.get(
                    "x-next-cursor"
                )

                or response.headers.get(
                    "nextCursor"
                )

                or response.headers.get(
                    "Next-Cursor"
                )
            )

        else:

            rows = (

                payload.get(
                    "items"
                )

                or payload.get(
                    "data"
                )

                or payload.get(
                    "results"
                )

                or payload.get(
                    "activities"
                )

                or []
            )

            next_cursor = (

                payload.get(
                    "nextCursor"
                )

                or payload.get(
                    "next"
                )
            )

        # ==========================================
        # DEBUG LOGS
        # ==========================================
        logger.info(
            f"{client_id} "
            f"{table} "
            f"rows={len(rows)} "
            f"next_cursor={next_cursor}"
        )

        logger.info(
            f"{client_id} "
            f"{table} "
            f"headers={dict(response.headers)}"
        )

        logger.info(
            f"{client_id} "
            f"{table} "
            f"batch="
            f"{len(rows)}"
        )

        all_rows.extend(
            rows
        )

        if not next_cursor:

            break

    return all_rows

# =====================================================
# WRITE S3
# =====================================================

def write_s3(
    client,
    table,
    rows
):

    key = build_s3_key(
        client,
        SOURCE,
        table
    )

    payload = {

        "client":
            client,

        "table":
            table,

        "record_count":
            len(rows),

        "data":
            rows
    }

    write_json(
        BUCKET,
        key,
        payload
    )

    return key

# =====================================================
# MAIN
# =====================================================

def lambda_handler(
    event,
    context
):

    summary = {

        "success": [],
        "failed": []
    }

    clients = discover_client_secrets()

    for client_id, client_secret in clients.items():

        for table in TABLES:

            try:

                start_ts = get_watermark(

                    client_id,

                    SOURCE,

                    table
                )

                rows = fetch_ninjaone_data(

                    client_id,

                    client_secret,

                    table,

                    start_ts
                )

                rows = filter_new_records(

                    rows,

                    table,

                    start_ts
                )

                if not rows:

                    continue

                s3_key = write_s3(

                    client_id,

                    table,

                    rows
                )

                max_ts = get_max_timestamp(

                    rows,

                    TABLE_TIMESTAMP_FIELD[
                        table
                    ]
                )

                if max_ts:

                    update_watermark(

                        client_id,

                        SOURCE,

                        table,

                        max_ts
                    )

                summary[
                    "success"
                ].append({

                    "client":
                        client_id,

                    "table":
                        table,

                    "rows":
                        len(rows),

                    "s3_key":
                        s3_key,

                    "new_watermark":
                        max_ts
                })

            except Exception as e:

                traceback.print_exc()

                summary[
                    "failed"
                ].append({

                    "client":
                        client_id,

                    "table":
                        table,

                    "error":
                        str(e)
                })

    return {

        "statusCode":
            200,

        "body":
            json.dumps(
                summary,
                default=str
            )
    }










