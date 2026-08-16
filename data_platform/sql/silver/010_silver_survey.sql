-- Silver : referentiel des enquetes, deduplique et typé.
-- Bronze est append-only : on ne garde que la derniere version de chaque cle.
CREATE OR REPLACE TABLE silver_survey AS
WITH ranked AS (
    SELECT
        *,
        ROW_NUMBER() OVER (PARTITION BY id ORDER BY _ingested_at DESC) AS rn
    FROM bronze_surveys
)
SELECT
    id                                             AS survey_key,
    org_id,
    code                                           AS survey_code,
    title                                          AS survey_title,
    description                                    AS survey_description,
    status                                         AS survey_status,
    TRY_CAST(collection_services AS JSON)          AS collection_services_json,
    CAST(target_sample AS BIGINT)                  AS target_sample,
    CAST(start_date AS DATE)                       AS start_date,
    CAST(end_date AS DATE)                         AS end_date,
    default_language,
    CAST(created_at AS TIMESTAMP)                  AS created_at,
    -- Duree planifiee de collecte, utile au suivi de l'avancement.
    CASE
        WHEN start_date IS NOT NULL AND end_date IS NOT NULL
        THEN DATE_DIFF('day', CAST(start_date AS DATE), CAST(end_date AS DATE))
    END                                            AS planned_duration_days
FROM ranked
WHERE rn = 1;
