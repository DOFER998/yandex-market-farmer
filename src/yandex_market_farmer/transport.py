from curl_cffi.requests import Session


def make_session(jar):
    """Match upstream TLS family; let the profile supply coherent UA/client hints."""
    return Session(
        cookies=jar,
        impersonate="chrome146",
        default_headers=True,
        trust_env=False,
        proxies={"all": ""},  # libcurl itself also reads proxy environment variables.
        verify=True,
        allow_redirects=False,
        timeout=30,
    )
