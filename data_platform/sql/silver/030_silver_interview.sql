-- Silver : entretiens nettoyes, enrichis du contexte terrain et du controle qualite.
-- Un entretien et un seul par ligne ; les doublons de synchronisation sont ecartes.
CREATE OR REPLACE TABLE silver_interview AS
WITH ranked AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY id ORDER BY _ingested_at DESC) AS rn
    FROM bronze_interviews
),
enum_profile AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY _ingested_at DESC) AS rn
    FROM bronze_enumerators
),
assignment AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY id ORDER BY _ingested_at DESC) AS rn
    FROM bronze_assignments
),
unit AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY id ORDER BY _ingested_at DESC) AS rn
    FROM bronze_sample_units
)
SELECT
    i.id                                   AS interview_key,
    i.survey_id                            AS survey_key,
    i.questionnaire_id                     AS questionnaire_key,
    i.enumerator_id                        AS enumerator_key,
    i.assignment_id                        AS assignment_key,
    a.sample_unit_id                       AS sample_unit_key,
    i.collection_service,
    i.status                               AS interview_status,
    i.language,

    CAST(i.started_at AS TIMESTAMP)        AS started_at,
    CAST(i.ended_at AS TIMESTAMP)          AS ended_at,
    CAST(i.submitted_at AS TIMESTAMP)      AS submitted_at,
    CAST(i.started_at AS DATE)             AS interview_date,
    EXTRACT(hour FROM CAST(i.started_at AS TIMESTAMP)) AS interview_hour,

    -- Duree fiabilisee : on recalcule si la valeur applicative manque et on
    -- neutralise les valeurs aberrantes (negatives ou superieures a 8 heures).
    CASE
        WHEN i.duration_seconds IS NOT NULL AND i.duration_seconds BETWEEN 0 AND 28800
            THEN i.duration_seconds
        WHEN i.started_at IS NOT NULL AND i.ended_at IS NOT NULL
            THEN NULLIF(GREATEST(DATE_DIFF('second', CAST(i.started_at AS TIMESTAMP),
                                           CAST(i.ended_at AS TIMESTAMP)), 0), 0)
    END                                    AS duration_seconds,

    i.gps_accuracy_m,
    i.device_id,
    i.app_version,
    i.quality_score,
    TRY_CAST(i.quality_flags AS JSON)      AS quality_flags_json,
    LENGTH(COALESCE(TRY_CAST(i.quality_flags AS JSON), '[]'::JSON)) AS quality_flag_count,
    i.validated_by                         AS validated_by_key,
    CAST(i.validated_at AS TIMESTAMP)      AS validated_at,
    i.sync_batch_id,

    -- Statuts consolides : ce qui compte pour l'avancement et l'analyse.
    i.status IN ('completed', 'submitted', 'validated')                AS is_usable,
    i.status = 'validated'                                             AS is_validated,
    i.status = 'rejected'                                              AS is_rejected,
    i.status = 'partial'                                               AS is_partial,

    -- Contexte terrain
    ep.matricule                           AS enumerator_matricule,
    ep.base_zone                           AS enumerator_zone,
    ep.supervisor_id                       AS supervisor_key,
    u.geo_level_1,
    u.geo_level_2,
    u.geo_level_3,
    u.stratum,
    COALESCE(u.sampling_weight, 1.0)       AS sampling_weight,
    a.attempts                             AS assignment_attempts,

    i._ingested_at
FROM ranked i
LEFT JOIN enum_profile ep ON ep.user_id = i.enumerator_id AND ep.rn = 1
LEFT JOIN assignment a    ON a.id = i.assignment_id AND a.rn = 1
LEFT JOIN unit u          ON u.id = a.sample_unit_id AND u.rn = 1
WHERE i.rn = 1;
