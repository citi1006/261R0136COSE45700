from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from contextlib import closing
from datetime import datetime
from typing import Any

from app.config import DB_PATH


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with closing(get_connection()) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS analysis_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analyzed_at TEXT NOT NULL,
                store_name TEXT NOT NULL,
                cctv_id TEXT NOT NULL,
                cctv_nickname TEXT NOT NULL,
                roi_name TEXT NOT NULL,
                item_type TEXT NOT NULL,
                decision TEXT NOT NULL,
                confidence REAL NOT NULL,
                visible_ratio REAL NOT NULL,
                occlusion_duration REAL NOT NULL,
                brightness_mismatch_duration REAL NOT NULL,
                summary TEXT NOT NULL,
                source_path TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS cleanliness_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analyzed_at TEXT NOT NULL,
                store_name TEXT NOT NULL,
                cctv_id TEXT NOT NULL,
                cctv_nickname TEXT NOT NULL,
                roi_name TEXT NOT NULL,
                mode TEXT NOT NULL,
                decision TEXT NOT NULL,
                score INTEGER,
                confidence REAL NOT NULL,
                final_stage TEXT NOT NULL,
                summary TEXT NOT NULL,
                source_path TEXT,
                crop_path TEXT,
                exact_objects TEXT NOT NULL DEFAULT '[]',
                estimated_objects TEXT NOT NULL DEFAULT '[]',
                findings TEXT NOT NULL DEFAULT '[]',
                action_features TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        connection.commit()


def truncate_to_hour(timestamp: datetime) -> str:
    return timestamp.replace(minute=0, second=0, microsecond=0).isoformat(timespec="minutes")


def insert_result(record: dict[str, Any]) -> None:
    with closing(get_connection()) as connection:
        connection.execute(
            """
            INSERT INTO analysis_results (
                analyzed_at, store_name, cctv_id, cctv_nickname, roi_name, item_type,
                decision, confidence, visible_ratio, occlusion_duration,
                brightness_mismatch_duration, summary, source_path
            )
            VALUES (
                :analyzed_at, :store_name, :cctv_id, :cctv_nickname, :roi_name, :item_type,
                :decision, :confidence, :visible_ratio, :occlusion_duration,
                :brightness_mismatch_duration, :summary, :source_path
            )
            """,
            record,
        )
        connection.commit()


def fetch_results(filters: dict[str, str | None] | None = None) -> list[dict[str, Any]]:
    filters = filters or {}
    clauses: list[str] = []
    params: dict[str, Any] = {}
    for key in ("store_name", "cctv_id", "roi_name", "decision", "item_type"):
        value = filters.get(key)
        if value:
            clauses.append(f"{key} = :{key}")
            params[key] = value

    query = "SELECT * FROM analysis_results"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY analyzed_at DESC, id DESC"

    with closing(get_connection()) as connection:
        rows = connection.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def fetch_filter_options() -> dict[str, list[str]]:
    options: dict[str, list[str]] = {}
    with closing(get_connection()) as connection:
        for column in ("store_name", "cctv_id", "roi_name", "decision", "item_type"):
            rows = connection.execute(
                f"SELECT DISTINCT {column} AS value FROM analysis_results ORDER BY value"
            ).fetchall()
            options[column] = [row["value"] for row in rows if row["value"]]
    return options


def fetch_latest_by_roi(roi_name: str | None = None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}
    roi_clause = ""
    if roi_name:
        roi_clause = "WHERE roi_name = :roi_name"
        params["roi_name"] = roi_name

    query = f"""
        SELECT r1.*
        FROM analysis_results r1
        JOIN (
            SELECT cctv_id, roi_name, MAX(id) AS max_id
            FROM analysis_results
            {roi_clause}
            GROUP BY cctv_id, roi_name
        ) latest
        ON r1.id = latest.max_id
        ORDER BY
            CASE
                WHEN r1.decision = 'Absent' THEN 0
                WHEN r1.decision = 'Unknown' THEN 1
                ELSE 2
            END,
            r1.store_name,
            r1.cctv_nickname
    """
    with closing(get_connection()) as connection:
        rows = connection.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def clear_results() -> None:
    with closing(get_connection()) as connection:
        connection.execute("DELETE FROM analysis_results")
        connection.commit()


def bulk_insert(records: Iterable[dict[str, Any]]) -> None:
    with closing(get_connection()) as connection:
        connection.executemany(
            """
            INSERT INTO analysis_results (
                analyzed_at, store_name, cctv_id, cctv_nickname, roi_name, item_type,
                decision, confidence, visible_ratio, occlusion_duration,
                brightness_mismatch_duration, summary, source_path
            )
            VALUES (
                :analyzed_at, :store_name, :cctv_id, :cctv_nickname, :roi_name, :item_type,
                :decision, :confidence, :visible_ratio, :occlusion_duration,
                :brightness_mismatch_duration, :summary, :source_path
            )
            """,
            list(records),
        )
        connection.commit()


def insert_cleanliness_result(record: dict[str, Any]) -> None:
    with closing(get_connection()) as connection:
        connection.execute(
            """
            INSERT INTO cleanliness_results (
                analyzed_at, store_name, cctv_id, cctv_nickname, roi_name,
                mode, decision, score, confidence, final_stage, summary,
                source_path, crop_path, exact_objects, estimated_objects,
                findings, action_features
            )
            VALUES (
                :analyzed_at, :store_name, :cctv_id, :cctv_nickname, :roi_name,
                :mode, :decision, :score, :confidence, :final_stage, :summary,
                :source_path, :crop_path, :exact_objects, :estimated_objects,
                :findings, :action_features
            )
            """,
            record,
        )
        connection.commit()


def fetch_cleanliness_results(filters: dict[str, str | None] | None = None) -> list[dict[str, Any]]:
    filters = filters or {}
    clauses: list[str] = []
    params: dict[str, Any] = {}
    for key in ("store_name", "cctv_id", "roi_name", "mode", "decision", "final_stage"):
        value = filters.get(key)
        if value:
            clauses.append(f"{key} = :{key}")
            params[key] = value

    query = "SELECT * FROM cleanliness_results"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY analyzed_at DESC, id DESC"

    with closing(get_connection()) as connection:
        rows = connection.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def fetch_cleanliness_store_summary(filters: dict[str, str | None] | None = None) -> list[dict[str, Any]]:
    filters = filters or {}
    clauses: list[str] = []
    params: dict[str, Any] = {}
    for key in ("store_name", "cctv_id", "roi_name", "mode", "decision", "final_stage"):
        value = filters.get(key)
        if value:
            clauses.append(f"{key} = :{key}")
            params[key] = value

    where_clause = ""
    if clauses:
        where_clause = "WHERE " + " AND ".join(clauses)

    query = """
        SELECT
            store_name,
            COUNT(*) AS total_count,
            SUM(CASE WHEN decision = 'cleaned_likely' THEN 1 ELSE 0 END) AS cleaned_count,
            SUM(CASE WHEN decision = 'needs_check' THEN 1 ELSE 0 END) AS needs_check_count,
            SUM(CASE WHEN decision = 'unknown' THEN 1 ELSE 0 END) AS unknown_count,
            AVG(confidence) AS average_confidence,
            AVG(score) AS average_score,
            MAX(analyzed_at) AS latest_analyzed_at
        FROM cleanliness_results
        {where_clause}
        GROUP BY store_name
        ORDER BY needs_check_count DESC, unknown_count DESC, store_name
    """.format(where_clause=where_clause)
    with closing(get_connection()) as connection:
        rows = connection.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def fetch_cleanliness_filter_options() -> dict[str, list[str]]:
    options: dict[str, list[str]] = {}
    with closing(get_connection()) as connection:
        for column in ("store_name", "cctv_id", "roi_name", "mode", "decision", "final_stage"):
            rows = connection.execute(
                f"SELECT DISTINCT {column} AS value FROM cleanliness_results ORDER BY value"
            ).fetchall()
            options[column] = [row["value"] for row in rows if row["value"]]
    return options


def clear_cleanliness_results() -> None:
    with closing(get_connection()) as connection:
        connection.execute("DELETE FROM cleanliness_results")
        connection.commit()
