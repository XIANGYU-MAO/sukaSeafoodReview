from __future__ import annotations

from typing import Any


FIXED_ROUTE_METHODS = {
    "/v1/health": {"get"},
    "/v1/auth/names": {"get"},
    "/v1/auth/login": {"post"},
    "/v1/auth/me": {"get"},
    "/v1/auth/logout": {"post"},
    "/v1/auth/change-password": {"post"},
    "/v1/reviews/current": {"post"},
    "/v1/reviews/{candidate_id}/decision": {"post"},
    "/v1/progress": {"get"},
    "/v1/history": {"get"},
    "/v1/history/{review_id}": {"patch"},
    "/v1/admin/users": {"get"},
    "/v1/admin/settings": {"get", "patch"},
    "/v1/admin/sources": {"get"},
    "/v1/admin/species": {"get", "post"},
    "/v1/admin/species/{species_id}": {"patch"},
    "/v1/admin/candidates": {"get"},
    "/v1/admin/candidates/bulk-disable": {"post"},
    "/v1/admin/candidates/{candidate_id}": {"patch"},
    "/v1/admin/reviews": {"get"},
    "/v1/admin/reviews/{review_id}": {"patch"},
    "/v1/admin/current": {"get"},
    "/v1/admin/current/{candidate_id}/release": {"post"},
    "/v1/admin/current/{candidate_id}/transfer": {"post"},
    "/v1/admin/reviews/{review_id}/reopen": {"post"},
    "/v1/admin/users/{user_id}/reset-password": {"post"},
    "/v1/admin/imports/preview": {"post"},
    "/v1/admin/imports/commit": {"post"},
    "/v1/admin/exports": {"get", "post"},
    "/v1/admin/exports/pending-counts": {"get"},
    "/v1/admin/exports/{batch_id}.csv": {"get"},
    "/v1/admin/exports/{batch_id}/receipt-file": {"post"},
    "/v1/sync/batches/{batch_id}/receipt": {"post"},
}


def operation_methods(path_item: dict[str, Any]) -> set[str]:
    return set(path_item) & {"get", "post", "put", "patch", "delete"}


def dereference(schema: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    reference = schema.get("$ref")
    if reference is None:
        return schema
    return components[reference.rsplit("/", 1)[-1]]


def recursive_property_names(schema: dict[str, Any], components: dict[str, Any]) -> set[str]:
    schema = dereference(schema, components)
    names = set(schema.get("properties", {}))
    for child in schema.get("properties", {}).values():
        names.update(recursive_property_names(child, components))
    if "items" in schema:
        names.update(recursive_property_names(schema["items"], components))
    for keyword in ("anyOf", "oneOf", "allOf"):
        for child in schema.get(keyword, []):
            names.update(recursive_property_names(child, components))
    return names


def test_fixed_routes_and_methods_remain_documented(client):
    paths = client.app.openapi()["paths"]

    for path, methods in FIXED_ROUTE_METHODS.items():
        assert path in paths
        assert operation_methods(paths[path]) == methods


def test_registration_social_login_and_original_image_transport_are_absent(client):
    openapi = client.app.openapi()
    normalized_paths = " ".join(openapi["paths"]).lower()

    assert "register" not in normalized_paths
    assert "signup" not in normalized_paths
    assert "oauth" not in normalized_paths
    assert "social" not in normalized_paths
    assert "image/upload" not in normalized_paths
    assert "images/upload" not in normalized_paths
    assert "image/proxy" not in normalized_paths
    assert "original-image" not in normalized_paths
    for path_item in openapi["paths"].values():
        for method in operation_methods(path_item):
            media_types = path_item[method].get("requestBody", {}).get("content", {})
            assert not any(media_type.startswith("image/") for media_type in media_types)


def test_reviewer_progress_recursively_contains_only_aggregates(client):
    openapi = client.app.openapi()
    components = openapi["components"]["schemas"]
    response_schema = openapi["paths"]["/v1/progress"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    fields = recursive_property_names(response_schema, components)

    assert fields.isdisjoint({"id", "candidate_id", "review_id", "notes", "history", "items"})
    assert all(not field.endswith("_url") for field in fields)


def test_import_export_media_types_and_pagination_are_explicit(client):
    openapi = client.app.openapi()
    paths = openapi["paths"]
    components = openapi["components"]["schemas"]

    preview = paths["/v1/admin/imports/preview"]["post"]
    assert set(preview["requestBody"]["content"]) == {"multipart/form-data"}
    upload_schema = dereference(
        preview["requestBody"]["content"]["multipart/form-data"]["schema"], components
    )
    assert upload_schema["required"] == ["file"]

    receipt = paths["/v1/admin/exports/{batch_id}/receipt-file"]["post"]
    assert set(receipt["requestBody"]["content"]) == {"application/json"}
    csv_download = paths["/v1/admin/exports/{batch_id}.csv"]["get"]
    assert operation_methods(paths["/v1/admin/exports/{batch_id}.csv"]) == {"get"}
    csv_content = csv_download["responses"]["200"]["content"]
    assert set(csv_content) == {"text/csv"}
    assert csv_content["text/csv"]["schema"]["type"] == "string"
    assert "application/json" not in csv_content

    export_parameters = {
        parameter["name"]: parameter
        for parameter in paths["/v1/admin/exports"]["get"]["parameters"]
    }
    assert set(export_parameters) == {"limit", "offset"}
    assert export_parameters["limit"]["schema"] == {
        "type": "integer",
        "maximum": 100,
        "minimum": 1,
        "default": 100,
        "title": "Limit",
    }
    assert export_parameters["offset"]["schema"]["minimum"] == 0
    assert operation_methods(paths["/v1/admin/sources"]) == {"get"}


def test_authentication_responses_keep_web_runtime_fields(client):
    openapi = client.app.openapi()
    components = openapi["components"]["schemas"]
    auth_schema = components["AuthState"]

    assert set(auth_schema["required"]) == {
        "id",
        "name",
        "role",
        "must_change_password",
        "csrf_token",
        "team_progress_visible",
    }
    for path in ("/v1/auth/login", "/v1/auth/me"):
        method = "post" if path.endswith("login") else "get"
        schema = openapi["paths"][path][method]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        assert schema == {"$ref": "#/components/schemas/AuthState"}

    names_schema = openapi["paths"]["/v1/auth/names"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    assert names_schema == {"$ref": "#/components/schemas/LoginOptionsResponse"}
