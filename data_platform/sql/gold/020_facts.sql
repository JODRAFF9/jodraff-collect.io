-- Gold : tables de faits du modele en etoile.

-- Fait principal : un entretien. Grain = un entretien.
CREATE OR REPLACE TABLE fact_interview AS
SELECT
    i.interview_key,
    i.survey_key,
    i.questionnaire_key,
    i.enumerator_key,
    i.assignment_key,
    i.collection_service,
    i.interview_status,
    COALESCE(
        MD5(COALESCE(i.geo_level_1, '~') || '|' || COALESCE(i.geo_level_2, '~')
            || '|' || COALESCE(i.geo_level_3, '~')),
        'UNKNOWN'
    )                                                  AS geography_key,
    CAST(STRFTIME(i.interview_date, '%Y%m%d') AS INTEGER) AS date_key,
    i.interview_date,
    i.interview_hour,
    i.stratum,

    -- Mesures
    1                                                  AS interview_count,
    CASE WHEN i.is_usable THEN 1 ELSE 0 END            AS usable_count,
    CASE WHEN i.is_validated THEN 1 ELSE 0 END         AS validated_count,
    CASE WHEN i.is_rejected THEN 1 ELSE 0 END          AS rejected_count,
    CASE WHEN i.is_partial THEN 1 ELSE 0 END           AS partial_count,
    i.duration_seconds,
    i.duration_seconds / 60.0                          AS duration_minutes,
    i.quality_score,
    i.quality_flag_count,
    i.sampling_weight,
    -- Effectif pondere : c'est cette mesure qui doit etre sommee pour toute
    -- statistique representative de la population, pas le simple comptage.
    CASE WHEN i.is_usable THEN i.sampling_weight ELSE 0 END AS weighted_usable,

    -- Indicateurs comportementaux issus des paradonnees
    p.event_count,
    p.change_count,
    p.correction_ratio,
    p.questions_touched,

    i.assignment_attempts,
    i.device_id,
    i.app_version,
    i.validated_at
FROM silver_interview i
LEFT JOIN silver_paradata_session p ON p.interview_key = i.interview_key;


-- Fait au grain le plus fin : une reponse. Alimente les tris a plat et croises.
CREATE OR REPLACE TABLE fact_answer AS
SELECT
    a.answer_key,
    a.interview_key,
    a.question_key,
    a.survey_key,
    a.enumerator_key,
    a.collection_service,
    a.question_code,
    a.question_type,
    a.repeat_index,
    CAST(STRFTIME(a.interview_date, '%Y%m%d') AS INTEGER) AS date_key,
    COALESCE(
        MD5(COALESCE(a.geo_level_1, '~') || '|' || COALESCE(a.geo_level_2, '~') || '|~'),
        'UNKNOWN'
    )                                                     AS geography_key,

    -- Valeurs
    a.value_text,
    a.value_number,
    a.value_date,
    COALESCE(a.choice_label, a.value_text)                AS value_label,
    a.choice_score,
    a.selected_count,

    -- Mesures
    1                                                     AS answer_count,
    CASE WHEN a.is_answered THEN 1 ELSE 0 END             AS answered_count,
    CASE WHEN NOT a.is_answered THEN 1 ELSE 0 END         AS missing_count,
    a.sampling_weight,
    CASE WHEN a.is_answered THEN a.sampling_weight ELSE 0 END AS weighted_answered,
    a.value_number * a.sampling_weight                    AS weighted_value
FROM silver_answer a;


-- Fait de suivi terrain : une ligne par enqueteur, par jour, par mode.
-- C'est la table de pilotage de la productivite du reseau.
CREATE OR REPLACE TABLE fact_fieldwork_daily AS
WITH interviews AS (
    SELECT
        enumerator_key,
        collection_service,
        interview_date,
        COUNT(*)                                        AS interviews_started,
        COUNT(*) FILTER (WHERE is_usable)               AS interviews_usable,
        COUNT(*) FILTER (WHERE is_rejected)             AS interviews_rejected,
        COUNT(*) FILTER (WHERE is_partial)              AS interviews_partial,
        AVG(duration_seconds)                           AS avg_duration_seconds,
        SUM(duration_seconds)                           AS total_duration_seconds,
        AVG(quality_score)                              AS avg_quality_score,
        MIN(started_at)                                 AS first_start,
        MAX(COALESCE(ended_at, started_at))             AS last_end
    FROM silver_interview
    WHERE enumerator_key IS NOT NULL AND interview_date IS NOT NULL
    GROUP BY enumerator_key, collection_service, interview_date
)
SELECT
    i.enumerator_key,
    i.collection_service,
    CAST(STRFTIME(i.interview_date, '%Y%m%d') AS INTEGER) AS date_key,
    i.interview_date,
    e.base_zone,
    e.supervisor_key,
    i.interviews_started,
    i.interviews_usable,
    i.interviews_rejected,
    i.interviews_partial,
    i.avg_duration_seconds,
    i.total_duration_seconds,
    i.avg_quality_score,
    e.daily_capacity,
    -- Rendement : realise rapporte a la capacite declaree.
    CASE WHEN e.daily_capacity > 0
         THEN CAST(i.interviews_usable AS DOUBLE) / e.daily_capacity END AS capacity_utilisation,
    -- Amplitude de la journee de travail, en heures.
    DATE_DIFF('second', i.first_start, i.last_end) / 3600.0 AS field_span_hours
FROM interviews i
LEFT JOIN silver_enumerator e ON e.enumerator_key = i.enumerator_key;


-- Fait de couverture de l'echantillon : ou en est-on sur le terrain.
CREATE OR REPLACE TABLE fact_sample_coverage AS
SELECT
    a.survey_key,
    a.collection_service,
    a.enumerator_key,
    COALESCE(
        MD5(COALESCE(a.geo_level_1, '~') || '|' || COALESCE(a.geo_level_2, '~')
            || '|' || COALESCE(a.geo_level_3, '~')),
        'UNKNOWN'
    )                                                    AS geography_key,
    a.stratum,
    COUNT(*)                                             AS assignments_total,
    COUNT(*) FILTER (WHERE a.is_done)                    AS assignments_done,
    COUNT(*) FILTER (WHERE a.is_open)                    AS assignments_open,
    COUNT(*) FILTER (WHERE a.is_overdue)                 AS assignments_overdue,
    SUM(a.attempts)                                      AS attempts_total,
    SUM(a.sampling_weight)                               AS weight_total,
    SUM(CASE WHEN a.is_done THEN a.sampling_weight ELSE 0 END) AS weight_covered
FROM silver_assignment a
GROUP BY ALL;


-- Suivi des quotas : cible, realise, reste a faire.
CREATE OR REPLACE TABLE fact_quota AS
WITH q AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY id ORDER BY _ingested_at DESC) AS rn
    FROM bronze_quotas
)
SELECT
    id                                   AS quota_key,
    survey_id                            AS survey_key,
    label                                AS quota_label,
    TRY_CAST(dimensions AS JSON)         AS dimensions_json,
    target                               AS quota_target,
    achieved                             AS quota_achieved,
    GREATEST(target - achieved, 0)       AS quota_remaining,
    CASE WHEN target > 0
         THEN CAST(achieved AS DOUBLE) / target END AS quota_completion_rate,
    achieved >= target                   AS is_full,
    is_blocking
FROM q
WHERE rn = 1;
