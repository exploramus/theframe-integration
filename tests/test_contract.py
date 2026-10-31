"""The integration's client and the add-on's API agree (docs/DESIGN.md 3.6).

The add-on's OpenAPI document is the contract; every endpoint the integration
calls must be in it, with the same method.
"""

import pytest


@pytest.fixture(scope="session")
def openapi(theframe):
    return theframe.controller.app.openapi()


def test_every_client_endpoint_is_in_the_addon_openapi(client_api, openapi):
    missing = [
        "{} {}".format(method, path)
        for method, path in client_api.ENDPOINTS.values()
        if method.lower() not in openapi["paths"].get(path, {})
    ]
    assert missing == []


def test_slideshow_fields_the_integration_sets_are_accepted(openapi):
    body = openapi["paths"]["/api/v1/slideshow"]["put"]["requestBody"]["content"]["application/json"]["schema"]
    schema = openapi["components"]["schemas"][body["$ref"].rsplit("/", 1)[1]]
    assert {"enabled", "interval_minutes", "mode", "keywords", "files", "position"} <= set(schema["properties"])
