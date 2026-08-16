-- Silver : referentiel enqueteurs, affectations et geographie.

CREATE OR REPLACE TABLE silver_enumerator AS
WITH ep AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY _ingested_at DESC) AS rn
    FROM bronze_enumerators
),
u AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY id ORDER BY _ingested_at DESC) AS rn
    FROM bronze_users
)
SELECT
    ep.user_id                                    AS enumerator_key,
    ep.matricule,
    -- Le nom complet reste disponible en silver pour la supervision nominative ;
    -- le gold expose en plus un libelle pseudonymise pour la diffusion large.
    u.full_name                                   AS enumerator_name,
    'ENQ-' || ep.matricule                        AS enumerator_pseudonym,
    ep.base_zone,
    ep.supervisor_id                              AS supervisor_key,
    CAST(ep.hired_at AS DATE)                     AS hired_at,
    ep.daily_capacity,
    TRY_CAST(ep.certified_services AS JSON)       AS certified_services_json,
    ep.training_completed,
    ep.status                                     AS enumerator_status,
    COALESCE(u.is_active, FALSE)                  AS is_active
FROM ep
LEFT JOIN u ON u.id = ep.user_id AND u.rn = 1
WHERE ep.rn = 1;


CREATE OR REPLACE TABLE silver_assignment AS
WITH a AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY id ORDER BY _ingested_at DESC) AS rn
    FROM bronze_assignments
),
unit AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY id ORDER BY _ingested_at DESC) AS rn
    FROM bronze_sample_units
)
SELECT
    a.id                                   AS assignment_key,
    a.survey_id                            AS survey_key,
    a.sample_unit_id                       AS sample_unit_key,
    a.enumerator_id                        AS enumerator_key,
    a.collection_service,
    a.status                               AS assignment_status,
    CAST(a.due_date AS DATE)               AS due_date,
    a.priority,
    COALESCE(a.attempts, 0)                AS attempts,
    a.outcome_code,
    CAST(a.created_at AS DATE)             AS created_date,
    a.status = 'done'                      AS is_done,
    a.status IN ('assigned', 'in_progress') AS is_open,
    -- Une affectation en retard n'est pas terminee et a depasse son echeance.
    (a.status IN ('assigned', 'in_progress')
     AND a.due_date IS NOT NULL
     AND CAST(a.due_date AS DATE) < CURRENT_DATE) AS is_overdue,
    u.geo_level_1,
    u.geo_level_2,
    u.geo_level_3,
    u.stratum,
    COALESCE(u.sampling_weight, 1.0)       AS sampling_weight
FROM a
LEFT JOIN unit u ON u.id = a.sample_unit_id AND u.rn = 1
WHERE a.rn = 1;


-- Geographie conforme, reconstituee a partir de l'echantillon effectif.
CREATE OR REPLACE TABLE silver_geography AS
WITH unit AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY id ORDER BY _ingested_at DESC) AS rn
    FROM bronze_sample_units
)
SELECT DISTINCT
    MD5(COALESCE(geo_level_1, '~') || '|' || COALESCE(geo_level_2, '~')
        || '|' || COALESCE(geo_level_3, '~'))  AS geography_key,
    geo_level_1,
    geo_level_2,
    geo_level_3
FROM unit
WHERE rn = 1
  AND (geo_level_1 IS NOT NULL OR geo_level_2 IS NOT NULL OR geo_level_3 IS NOT NULL);


-- Paradonnees agregees par entretien : base des indicateurs comportementaux.
CREATE OR REPLACE TABLE silver_paradata_session AS
WITH p AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY id ORDER BY _ingested_at DESC) AS rn
    FROM bronze_paradata
)
SELECT
    interview_id                                                   AS interview_key,
    COUNT(*)                                                       AS event_count,
    COUNT(*) FILTER (WHERE event_type = 'answer_changed')          AS change_count,
    COUNT(*) FILTER (WHERE event_type = 'answer_saved')            AS save_count,
    COUNT(DISTINCT question_code)                                  AS questions_touched,
    MIN(CAST(occurred_at AS TIMESTAMP))                            AS first_event_at,
    MAX(CAST(occurred_at AS TIMESTAMP))                            AS last_event_at,
    AVG(duration_ms)                                               AS avg_question_duration_ms,
    -- Un ratio de corrections eleve signale une saisie hesitante ou douteuse.
    CASE
        WHEN COUNT(*) FILTER (WHERE event_type = 'answer_saved') > 0
        THEN CAST(COUNT(*) FILTER (WHERE event_type = 'answer_changed') AS DOUBLE)
             / COUNT(*) FILTER (WHERE event_type = 'answer_saved')
    END                                                            AS correction_ratio
FROM p
WHERE rn = 1
GROUP BY interview_id;
