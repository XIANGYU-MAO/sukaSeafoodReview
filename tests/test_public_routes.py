from __future__ import annotations

import os

import pytest
import requests


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_PUBLIC_ROUTE_TESTS") != "1",
    reason="RUN_PUBLIC_ROUTE_TESTS=1 is required for authorized live checks",
)


@pytest.mark.parametrize(
    "url",
    [
        "https://findai.top/project",
        "https://findai.top/project/seafood",
        "https://findai.top/project-assets/app.js",
        "https://www.findai.top/project",
    ],
)
def test_removed_project_is_404(url):
    assert requests.get(url, timeout=15, allow_redirects=False).status_code == 404


def test_review_and_api_health_are_content_aware():
    review = requests.get(
        "https://findai.top/sukaseafood/review", timeout=15, allow_redirects=False
    )
    health = requests.get(
        "https://findai.top/sukaseafood/api/v1/health",
        timeout=15,
        allow_redirects=False,
    )
    assert review.status_code == 200 and "SukaSeafood" in review.text
    assert health.status_code == 200 and health.json() == {"status": "ok"}


def test_www_sukaseafood_redirects_to_root_domain():
    response = requests.get(
        "https://www.findai.top/sukaseafood/review",
        timeout=15,
        allow_redirects=False,
    )
    assert response.status_code in {301, 308}
    assert response.headers["location"].startswith(
        "https://findai.top/sukaseafood/review"
    )
