from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from flask.testing import FlaskClient

import config
from utils import path_manager
from utils.db import connection as db_connection
from utils.db import insert_classification, insert_detection, insert_image
from web.services.filter_service import FilterContext, resolve_filtered_ids
from web.web_interface import create_web_interface


def _reset_test_config(monkeypatch, tmp_path):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    monkeypatch.setenv("OUTPUT_DIR", str(output_dir))
    monkeypatch.setenv("EDIT_PASSWORD", "test-password")
    config._CONFIG = None
    db_connection._schema_initialized_paths.clear()
    path_manager._instance = None
    return output_dir


def _seed_detection(
    conn,
    *,
    filename: str,
    timestamp: str,
    species: str,
    review_status: str = "confirmed_bird",
    detection_status: str = "active",
    decision_state: str | None = "confirmed",
    score: float = 0.95,
    is_favorite: int = 0,
    bbox: tuple[float, float, float, float] = (0.18, 0.16, 0.22, 0.24),
) -> int:
    insert_image(
        conn,
        {
            "filename": filename,
            "timestamp": timestamp,
            "source_id": 1,
            "content_hash": f"hash-{filename}",
        },
    )
    conn.execute(
        "UPDATE images SET review_status = ? WHERE filename = ?",
        (review_status, filename),
    )
    detection_id = insert_detection(
        conn,
        {
            "image_filename": filename,
            "bbox_x": bbox[0],
            "bbox_y": bbox[1],
            "bbox_w": bbox[2],
            "bbox_h": bbox[3],
            "od_class_name": "bird",
            "od_confidence": 0.93,
            "od_model_id": "yolo-test",
            "created_at": timestamp,
            "score": score,
            "decision_state": decision_state,
            "decision_level": "species",
            "thumbnail_path": filename.replace(".jpg", "_crop_1.webp"),
        },
    )
    conn.execute(
        "UPDATE detections SET status = ?, is_favorite = ? WHERE detection_id = ?",
        (detection_status, is_favorite, detection_id),
    )
    insert_classification(
        conn,
        {
            "detection_id": detection_id,
            "cls_class_name": species,
            "cls_confidence": 0.97,
            "cls_model_id": "cls-test",
            "rank": 1,
            "created_at": timestamp,
        },
    )
    conn.commit()
    return detection_id


@pytest.fixture
def local_db_app(monkeypatch, tmp_path):
    _reset_test_config(monkeypatch, tmp_path)

    detection_manager = MagicMock()
    detection_manager.frame_lock = nullcontext()
    detection_manager.latest_raw_timestamp = 0.0
    detection_manager.last_good_frame_timestamp = 0.0
    detection_manager._first_frame_received = False

    with (
        patch(
            "web.services.auth_service.should_require_password_setup",
            return_value=False,
        ),
        patch("web.services.auth_service.is_default_password", return_value=False),
    ):
        app = create_web_interface(detection_manager)
        app.config["TESTING"] = True

        import web.blueprints.review as review_blueprint

        review_blueprint.config = config.get_config()
        yield app


@pytest.fixture
def favorites_fixture(local_db_app):
    """Two species: Parus_major (favorited) and Pica_pica (not favorited),
    both on the same day so they share the daily sub-gallery."""
    today_iso = datetime.now().strftime("%Y-%m-%d")
    p = today_iso.replace("-", "")

    with db_connection.closing_connection() as conn:
        fav_id = _seed_detection(
            conn,
            filename=f"{p}_120000_fav.jpg",
            timestamp=f"{p}_120000",
            species="Parus_major",
            score=0.98,
            is_favorite=1,
        )
        plain_id = _seed_detection(
            conn,
            filename=f"{p}_121000_plain.jpg",
            timestamp=f"{p}_121000",
            species="Pica_pica",
            score=0.97,
            is_favorite=0,
        )

    with local_db_app.test_client() as client:
        with client.session_transaction() as session:
            session["authenticated"] = True
        yield client, today_iso, fav_id, plain_id


def test_species_overview_keeps_history_despite_favorites_parameter(favorites_fixture):
    client, _today, fav_id, plain_id = favorites_fixture

    resp = client.get("/species/overview?species_key=Pica_pica&favorites=1")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert f'data-detection-id="{plain_id}"' in body
    assert "No detections found for this species" not in body


def test_species_overview_favorites_only_keeps_favorite(favorites_fixture):
    client, _today, fav_id, _plain_id = favorites_fixture

    resp = client.get("/species/overview?species_key=Parus_major&favorites=1")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert f'data-detection-id="{fav_id}"' in body


def test_species_overview_no_param_unchanged(favorites_fixture):
    client, _today, _fav_id, plain_id = favorites_fixture

    resp = client.get("/species/overview?species_key=Pica_pica")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert f'data-detection-id="{plain_id}"' in body


def test_species_overview_favorites_only_inline_context(favorites_fixture):
    client, _today, _fav_id, _plain_id = favorites_fixture

    resp = client.get("/species/overview?species_key=Parus_major&favorites=1")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "favorites_only: false" in body


def test_species_grid_preserves_membership_without_favorites(favorites_fixture):
    client, _today, _fav_id, _plain_id = favorites_fixture

    resp = client.get("/species?favorites=1")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "species_key=Parus_major" in body
    assert "species_key=Pica_pica" in body
    assert 'name="source"' not in body
    assert 'name="start"' in body and 'name="end"' in body
    assert 'id="filter-favorites-select"' not in body


def test_species_grid_no_param_shows_all(favorites_fixture):
    client, _today, _fav_id, _plain_id = favorites_fixture

    resp = client.get("/species")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "species_key=Parus_major" in body
    assert "species_key=Pica_pica" in body


def test_favorites_page_shows_every_favorite_photo_across_species(favorites_fixture):
    client, today_iso, fav_id, plain_id = favorites_fixture
    p = today_iso.replace("-", "")
    older_date = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    with db_connection.closing_connection() as conn:
        same_species_fav_id = _seed_detection(
            conn,
            filename=f"{p}_120500_same_species_fav.jpg",
            timestamp=f"{p}_120500",
            species="Parus_major",
            is_favorite=1,
        )
        other_species_fav_id = _seed_detection(
            conn,
            filename=f"{older_date}_121000_other_species_fav.jpg",
            timestamp=f"{older_date}_121000",
            species="Pica_pica",
            is_favorite=1,
        )

    response = client.get("/favorites")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "<title>Favorites - WatchMyBirds</title>" in body
    assert 'id="favorites-grid"' in body
    assert "All favorite photos" in body
    assert "3 favorite photos" in body
    for detection_id in (fav_id, same_species_fav_id, other_species_fav_id):
        assert f'data-detection-id="{detection_id}"' in body
    assert f'data-detection-id="{plain_id}"' not in body
    assert 'href="/species/overview?species_key=' not in body
    assert 'id="favorites-select-all"' in body
    assert 'id="favorites-batch-actions" hidden' in body
    assert 'class="form-check-input wm-tile__checkbox"' in body
    assert (
        '<select id="favorites-species-filter" name="species" onchange="this.form.submit()">'
        in body
    )
    assert (
        '<select id="favorites-sort" name="sort" onchange="this.form.submit()">' in body
    )
    assert ">Apply</button>" not in body
    assert 'id="favorites-unfavorite-selected"' in body
    assert "Download Selected (ZIP)" in body


def test_authenticated_favorites_page_shows_unfavorite_action(
    favorites_fixture: tuple[FlaskClient, str, int, int],
) -> None:
    client, _today_iso, _favorite_id, _plain_id = favorites_fixture
    with client.session_transaction() as session:
        session["authenticated"] = True

    response = client.get("/favorites")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="favorites-unfavorite-selected"' in body
    assert "window.unfavoriteSelectedFavorites = async function" in body


def _anonymous_favorites_csrf_token(client: FlaskClient) -> str:
    with client.session_transaction() as session:
        session.pop("authenticated", None)
    response = client.get("/favorites")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'id="favorites-batch-actions" hidden' in body
    assert "Unfavorite Selected" not in body
    with client.session_transaction() as session:
        return str(session["_csrf_token"])


def test_favorites_download_is_available_without_login(
    favorites_fixture: tuple[FlaskClient, str, int, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _today_iso, favorite_id, _plain_id = favorites_fixture
    csrf_token = _anonymous_favorites_csrf_token(client)
    archive = BytesIO(b"favorite-zip")
    build_archive = MagicMock(return_value=archive)
    monkeypatch.setattr(
        "web.blueprints.gallery.image_download_service.build_download_archive",
        build_archive,
    )

    response = client.post(
        "/api/favorites/download",
        json={"detection_ids": [favorite_id]},
        headers={"X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 200
    assert response.mimetype == "application/zip"
    assert response.data == b"favorite-zip"
    assert response.headers["Content-Disposition"].startswith("attachment;")
    build_archive.assert_called_once_with([favorite_id])


def test_favorites_download_rejects_non_favorites(
    favorites_fixture: tuple[FlaskClient, str, int, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _today_iso, favorite_id, plain_id = favorites_fixture
    csrf_token = _anonymous_favorites_csrf_token(client)
    build_archive = MagicMock()
    monkeypatch.setattr(
        "web.blueprints.gallery.image_download_service.build_download_archive",
        build_archive,
    )

    response = client.post(
        "/api/favorites/download",
        json={"detection_ids": [favorite_id, plain_id]},
        headers={"X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 400
    build_archive.assert_not_called()


def test_authenticated_user_can_unfavorite_selected(
    favorites_fixture: tuple[FlaskClient, str, int, int],
) -> None:
    client, _today_iso, favorite_id, _plain_id = favorites_fixture
    client.get("/favorites")
    with client.session_transaction() as session:
        csrf_token = str(session["_csrf_token"])

    response = client.post(
        "/api/favorites/unfavorite",
        json={"detection_ids": [favorite_id]},
        headers={"X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 200
    assert response.get_json()["unfavorited_ids"] == [favorite_id]
    with db_connection.closing_connection() as conn:
        row = conn.execute(
            "SELECT is_favorite, rating_source FROM detections WHERE detection_id = ?",
            (favorite_id,),
        ).fetchone()
    assert row["is_favorite"] == 0
    assert row["rating_source"] == "manual"
    assert f'data-detection-id="{favorite_id}"' not in client.get(
        "/favorites"
    ).get_data(as_text=True)


def test_unfavorite_rejects_stale_or_nonfavorite_selection(
    favorites_fixture: tuple[FlaskClient, str, int, int],
) -> None:
    client, _today_iso, favorite_id, plain_id = favorites_fixture
    client.get("/favorites")
    with client.session_transaction() as session:
        csrf_token = str(session["_csrf_token"])

    response = client.post(
        "/api/favorites/unfavorite",
        json={"detection_ids": [favorite_id, plain_id]},
        headers={"X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 409
    assert f'data-detection-id="{favorite_id}"' in client.get("/favorites").get_data(
        as_text=True
    )


def test_anonymous_user_cannot_unfavorite(
    favorites_fixture: tuple[FlaskClient, str, int, int],
) -> None:
    client, _today_iso, favorite_id, _plain_id = favorites_fixture
    csrf_token = _anonymous_favorites_csrf_token(client)

    response = client.post(
        "/api/favorites/unfavorite",
        json={"detection_ids": [favorite_id]},
        headers={"X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert f'data-detection-id="{favorite_id}"' in client.get("/favorites").get_data(
        as_text=True
    )


def test_favorites_page_empty_state(local_db_app):
    today_iso = datetime.now().strftime("%Y-%m-%d")
    p = today_iso.replace("-", "")
    with db_connection.closing_connection() as conn:
        _seed_detection(
            conn,
            filename=f"{p}_133000_none.jpg",
            timestamp=f"{p}_133000",
            species="Pica_pica",
            is_favorite=0,
        )

    with local_db_app.test_client() as client:
        response = client.get("/favorites?sort=species")

    assert response.status_code == 200
    assert "No favorite photos yet." in response.get_data(as_text=True)


def test_favorites_page_paginates_all_favorite_photos(favorites_fixture, monkeypatch):
    client, _today_iso, fav_id, _plain_id = favorites_fixture
    older_date = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    with db_connection.closing_connection() as conn:
        older_fav_id = _seed_detection(
            conn,
            filename=f"{older_date}_121000_older_fav.jpg",
            timestamp=f"{older_date}_121000",
            species="Pica_pica",
            is_favorite=1,
        )

    monkeypatch.setattr("web.blueprints.gallery.PAGE_SIZE", 1)
    first_page = client.get("/favorites")
    first_body = first_page.get_data(as_text=True)
    second_page = client.get("/favorites?page=2")
    second_body = second_page.get_data(as_text=True)

    assert first_page.status_code == second_page.status_code == 200
    assert "Page 1 of 2 (2 favorite photos)" in first_body
    assert 'href="/favorites?page=2"' in first_body
    assert f'data-detection-id="{fav_id}"' in first_body
    assert f'data-detection-id="{older_fav_id}"' not in first_body
    assert "Page 2 of 2 (2 favorite photos)" in second_body
    assert f'data-detection-id="{older_fav_id}"' in second_body
    assert f'data-detection-id="{fav_id}"' not in second_body


def test_favorites_filter_uses_localized_species_name(favorites_fixture):
    client, _today_iso, fav_id, _plain_id = favorites_fixture
    older_date = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    with db_connection.closing_connection() as conn:
        pica_id = _seed_detection(
            conn,
            filename=f"{older_date}_121000_pica_fav.jpg",
            timestamp=f"{older_date}_121000",
            species="Pica_pica",
            is_favorite=1,
        )

    response = client.get("/favorites?species=Pica_pica")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert '<option value="all">All species</option>' in body
    assert '<option value="Pica_pica" selected>Elster</option>' in body
    assert f'data-detection-id="{pica_id}"' in body
    assert f'data-detection-id="{fav_id}"' not in body


def test_favorites_sort_by_localized_species_name(favorites_fixture):
    client, _today_iso, parus_id, _plain_id = favorites_fixture
    older_date = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    with db_connection.closing_connection() as conn:
        pica_id = _seed_detection(
            conn,
            filename=f"{older_date}_121000_pica_fav.jpg",
            timestamp=f"{older_date}_121000",
            species="Pica_pica",
            is_favorite=1,
        )

    response = client.get("/favorites?sort=species")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert '<option value="species" selected>Species (A–Z)</option>' in body
    assert body.index(f'data-detection-id="{pica_id}"') < body.index(
        f'data-detection-id="{parus_id}"'
    )


def test_favorites_sort_by_date_keeps_newest_first(favorites_fixture):
    client, _today_iso, parus_id, _plain_id = favorites_fixture
    older_date = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    with db_connection.closing_connection() as conn:
        pica_id = _seed_detection(
            conn,
            filename=f"{older_date}_121000_pica_fav.jpg",
            timestamp=f"{older_date}_121000",
            species="Pica_pica",
            is_favorite=1,
        )

    response = client.get("/favorites?sort=date")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert '<option value="date" selected>Date (newest first)</option>' in body
    assert body.index(f'data-detection-id="{parus_id}"') < body.index(
        f'data-detection-id="{pica_id}"'
    )


def test_subgallery_favorites_only_keeps_only_favorite_observations(favorites_fixture):
    client, today_iso, fav_id, plain_id = favorites_fixture

    resp = client.get(f"/gallery/{today_iso}?favorites=1")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert f'data-detection-id="{fav_id}"' in body
    assert f'data-detection-id="{plain_id}"' not in body


def test_subgallery_no_param_shows_both(favorites_fixture):
    client, today_iso, fav_id, plain_id = favorites_fixture

    resp = client.get(f"/gallery/{today_iso}")
    body = resp.get_data(as_text=True)
    assert f'data-detection-id="{fav_id}"' in body
    assert f'data-detection-id="{plain_id}"' in body


def test_subgallery_favorites_only_prunes_nonfavorite_companion(local_db_app):
    """A SINGLE observation with a favorite + a non-favorite member of the
    SAME species renders only the favorite member in favorites-only mode.
    This is the core anti-leak guarantee."""
    today_iso = datetime.now().strftime("%Y-%m-%d")
    p = today_iso.replace("-", "")
    with db_connection.closing_connection() as conn:
        fav_id = _seed_detection(
            conn,
            filename=f"{p}_140000_mixed_fav.jpg",
            timestamp=f"{p}_140000",
            species="Parus_major",
            score=0.95,
            is_favorite=1,
        )
        plain_id = _seed_detection(
            conn,
            filename=f"{p}_140030_mixed_plain.jpg",
            timestamp=f"{p}_140030",
            species="Parus_major",
            score=0.99,
            is_favorite=0,
        )

    with local_db_app.test_client() as client:
        with client.session_transaction() as session:
            session["authenticated"] = True
        resp = client.get(f"/gallery/{today_iso}?favorites=1")
        body = resp.get_data(as_text=True)
        assert f'data-detection-id="{fav_id}"' in body
        assert f'data-detection-id="{plain_id}"' not in body


def test_subgallery_favorites_only_empty_state(local_db_app):
    """A day with detections but zero favorites shows the empty message."""
    today_iso = datetime.now().strftime("%Y-%m-%d")
    p = today_iso.replace("-", "")
    with db_connection.closing_connection() as conn:
        _seed_detection(
            conn,
            filename=f"{p}_133000_none.jpg",
            timestamp=f"{p}_133000",
            species="Pica_pica",
            score=0.9,
            is_favorite=0,
        )
    with local_db_app.test_client() as client:
        with client.session_transaction() as session:
            session["authenticated"] = True
        resp = client.get(f"/gallery/{today_iso}?favorites=1")
        assert resp.status_code == 200
        assert "No observations match the filter." in resp.get_data(as_text=True)


def test_filter_bar_renders_favorites_dropdown_unselected(favorites_fixture):
    client, today, _fav_id, _plain_id = favorites_fixture
    resp = client.get(f"/gallery/{today}")
    body = resp.get_data(as_text=True)
    assert 'id="filter-favorites-select"' in body
    # "All photos" selected by default (no favorites param).
    assert '<option value="" selected' in body or '<option value=""  selected' in body


def test_filter_bar_marks_favorites_selected_when_active(favorites_fixture):
    client, today, _fav_id, _plain_id = favorites_fixture
    resp = client.get(f"/gallery/{today}?favorites=1")
    body = resp.get_data(as_text=True)
    assert 'value="1" selected' in body


def test_all_filtered_species_overview_matches_current_history(favorites_fixture):
    _client, today_iso, fav_id, _plain_id = favorites_fixture
    p = today_iso.replace("-", "")
    with db_connection.closing_connection() as conn:
        same_species_plain_id = _seed_detection(
            conn,
            filename=f"{p}_150000_same_species_plain.jpg",
            timestamp=f"{p}_150000",
            species="Parus_major",
            score=0.97,
            is_favorite=0,
        )

    result = resolve_filtered_ids(
        FilterContext(
            surface="species_overview",
            species_key="Parus_major",
            favorites_only=True,
            min_score=0.0,
            min_score_explicit=True,
        )
    )

    assert fav_id in result.detection_ids
    assert same_species_plain_id in result.detection_ids


def test_all_filtered_gallery_respects_favorites(favorites_fixture):
    _client, today_iso, fav_id, plain_id = favorites_fixture

    result = resolve_filtered_ids(
        FilterContext(
            surface="gallery",
            date=today_iso,
            favorites_only=True,
            min_score=0.0,
            min_score_explicit=True,
        )
    )

    assert fav_id in result.detection_ids
    assert plain_id not in result.detection_ids
