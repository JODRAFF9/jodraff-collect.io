-- Gold : vues de restitution pretes a l'emploi.
-- Elles alimentent directement Power BI et la generation des rapports LaTeX,
-- sans logique supplementaire cote outil de restitution.

-- Tri a plat pondere : la table de base de tout rapport d'enquete.
-- Les pourcentages sont calcules par fonction fenetre sur le total de la
-- question, ce qui evite une seconde passe d'agregation.
CREATE OR REPLACE VIEW mart_frequency_table AS
WITH base AS (
    SELECT
        f.survey_key,
        f.question_code,
        q.question_label,
        q.section_label,
        f.collection_service,
        COALESCE(f.value_label, 'Non renseigne') AS modality,
        f.sampling_weight
    FROM fact_answer f
    JOIN dim_question q ON q.question_key = f.question_key
    JOIN fact_interview i ON i.interview_key = f.interview_key
    WHERE q.analysis_role = 'dimension'
      AND i.usable_count = 1
),
counted AS (
    SELECT
        survey_key,
        question_code,
        question_label,
        section_label,
        collection_service,
        modality,
        COUNT(*)             AS n_raw,
        SUM(sampling_weight) AS n_weighted
    FROM base
    GROUP BY
        survey_key, question_code, question_label,
        section_label, collection_service, modality
)
SELECT
    survey_key,
    question_code,
    question_label,
    section_label,
    collection_service,
    modality,
    n_raw,
    n_weighted,
    n_raw * 100.0 / NULLIF(
        SUM(n_raw) OVER (PARTITION BY survey_key, question_code, collection_service), 0
    ) AS pct_raw,
    n_weighted * 100.0 / NULLIF(
        SUM(n_weighted) OVER (PARTITION BY survey_key, question_code, collection_service), 0
    ) AS pct_weighted
FROM counted;


-- Statistiques descriptives des variables quantitatives.
CREATE OR REPLACE VIEW mart_numeric_summary AS
SELECT
    f.survey_key,
    f.question_code,
    q.question_label,
    f.collection_service,
    COUNT(f.value_number)                                  AS n,
    AVG(f.value_number)                                    AS mean,
    -- Moyenne ponderee : l'estimateur correct sur echantillon a poids inegaux.
    SUM(f.value_number * f.sampling_weight)
        / NULLIF(SUM(f.sampling_weight), 0)                AS weighted_mean,
    MEDIAN(f.value_number)                                 AS median,
    STDDEV_SAMP(f.value_number)                            AS std_dev,
    MIN(f.value_number)                                    AS min_value,
    MAX(f.value_number)                                    AS max_value,
    QUANTILE_CONT(f.value_number, 0.25)                    AS p25,
    QUANTILE_CONT(f.value_number, 0.75)                    AS p75,
    -- Erreur type et demi-intervalle de confiance a 95 %.
    STDDEV_SAMP(f.value_number) / NULLIF(SQRT(COUNT(f.value_number)), 0) AS std_error,
    1.96 * STDDEV_SAMP(f.value_number)
        / NULLIF(SQRT(COUNT(f.value_number)), 0)           AS ci95_half_width
FROM fact_answer f
JOIN dim_question q ON q.question_key = f.question_key
JOIN fact_interview i ON i.interview_key = f.interview_key
WHERE f.value_number IS NOT NULL
  AND q.analysis_role = 'mesure'
  AND i.usable_count = 1
GROUP BY f.survey_key, f.question_code, q.question_label, f.collection_service;


-- Avancement de la collecte, par enquete et par mode.
CREATE OR REPLACE VIEW mart_collection_progress AS
SELECT
    s.survey_key,
    s.survey_code,
    s.survey_title,
    s.target_sample,
    f.collection_service,
    cs.service_name,
    COUNT(*)                                        AS interviews_total,
    SUM(f.usable_count)                             AS interviews_usable,
    SUM(f.validated_count)                          AS interviews_validated,
    SUM(f.rejected_count)                           AS interviews_rejected,
    SUM(f.weighted_usable)                          AS weighted_usable,
    AVG(f.duration_minutes)                         AS avg_duration_minutes,
    AVG(f.quality_score)                            AS avg_quality_score,
    MIN(f.interview_date)                           AS first_interview_date,
    MAX(f.interview_date)                           AS last_interview_date,
    COUNT(DISTINCT f.enumerator_key)                AS enumerators_active,
    -- Avancement rapporte a la cible de l'enquete, tous modes confondus.
    CAST(SUM(f.usable_count) AS DOUBLE) / NULLIF(s.target_sample, 0) AS completion_rate
FROM fact_interview f
JOIN dim_survey s ON s.survey_key = f.survey_key
LEFT JOIN dim_collection_service cs ON cs.collection_service = f.collection_service
GROUP BY
    s.survey_key, s.survey_code, s.survey_title, s.target_sample,
    f.collection_service, cs.service_name;


-- Classement de productivite et de qualite des enqueteurs.
CREATE OR REPLACE VIEW mart_enumerator_scorecard AS
SELECT
    e.enumerator_key,
    e.matricule,
    e.enumerator_name,
    e.base_zone,
    e.daily_capacity,
    d.collection_service,
    COUNT(DISTINCT d.interview_date)                       AS days_worked,
    SUM(d.interviews_started)                              AS interviews_started,
    SUM(d.interviews_usable)                               AS interviews_usable,
    SUM(d.interviews_rejected)                             AS interviews_rejected,
    CAST(SUM(d.interviews_usable) AS DOUBLE)
        / NULLIF(SUM(d.interviews_started), 0)             AS success_rate,
    CAST(SUM(d.interviews_rejected) AS DOUBLE)
        / NULLIF(SUM(d.interviews_started), 0)             AS rejection_rate,
    CAST(SUM(d.interviews_usable) AS DOUBLE)
        / NULLIF(COUNT(DISTINCT d.interview_date), 0)      AS interviews_per_day,
    AVG(d.avg_duration_seconds) / 60.0                     AS avg_duration_minutes,
    AVG(d.avg_quality_score)                               AS avg_quality_score,
    AVG(d.capacity_utilisation)                            AS avg_capacity_utilisation
FROM fact_fieldwork_daily d
JOIN dim_enumerator e ON e.enumerator_key = d.enumerator_key
GROUP BY
    e.enumerator_key, e.matricule, e.enumerator_name,
    e.base_zone, e.daily_capacity, d.collection_service;


-- Effet de mode : comparaison des resultats selon le canal de collecte.
-- Indispensable en dispositif multimode avant toute interpretation.
CREATE OR REPLACE VIEW mart_mode_effect AS
SELECT
    survey_key,
    question_code,
    question_label,
    modality,
    SUM(CASE WHEN collection_service = 'CAWI' THEN pct_weighted END) AS pct_cawi,
    SUM(CASE WHEN collection_service = 'CAPI' THEN pct_weighted END) AS pct_capi,
    SUM(CASE WHEN collection_service = 'CATI' THEN pct_weighted END) AS pct_cati,
    SUM(CASE WHEN collection_service = 'PAPI' THEN pct_weighted END) AS pct_papi,
    MAX(pct_weighted) - MIN(pct_weighted)                            AS spread_points,
    COUNT(DISTINCT collection_service)                               AS modes_observed
FROM mart_frequency_table
GROUP BY survey_key, question_code, question_label, modality
HAVING COUNT(DISTINCT collection_service) > 1;


-- Synthese qualite : ce que le superviseur doit regarder en priorite.
-- L'ecart de duree a la mediane de l'equipe est calcule en deux temps, la
-- mediane portant sur les moyennes deja agregees par enqueteur.
CREATE OR REPLACE VIEW mart_quality_overview AS
WITH per_enumerator AS (
    SELECT
        i.survey_key,
        i.collection_service,
        i.enumerator_key,
        e.matricule,
        e.enumerator_name,
        COUNT(*)                                          AS interviews,
        AVG(i.quality_score)                              AS avg_quality_score,
        COUNT(*) FILTER (WHERE i.quality_score < 0.6)     AS interviews_flagged,
        COUNT(*) FILTER (WHERE i.quality_flag_count > 0)  AS interviews_with_flags,
        AVG(i.duration_minutes)                           AS avg_duration_minutes,
        AVG(i.correction_ratio)                           AS avg_correction_ratio
    FROM fact_interview i
    LEFT JOIN dim_enumerator e ON e.enumerator_key = i.enumerator_key
    GROUP BY
        i.survey_key, i.collection_service, i.enumerator_key,
        e.matricule, e.enumerator_name
)
SELECT
    *,
    avg_duration_minutes - MEDIAN(avg_duration_minutes) OVER (
        PARTITION BY survey_key, collection_service
    ) AS duration_gap_to_team
FROM per_enumerator;
