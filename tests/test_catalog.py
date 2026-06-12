# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0


def test_list_catalog_apps(client):
    response = client.get("/api/catalog/apps")
    assert response.status_code == 200
    assert len(response.json["apps"]) == 2


def test_app_detail(client):
    response = client.get("/api/catalog/apps/wordpress")
    assert response.status_code == 200
    assert response.json["upstream_app_id"] == "wordpress"

