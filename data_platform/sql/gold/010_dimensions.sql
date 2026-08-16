-- Gold : dimensions du modele en etoile consomme par Power BI.
-- Chaque dimension a une cle de substitution stable et des attributs
-- directement utilisables comme axes d'analyse.

-- Dimension date : generee sur la plage effective de collecte, elargie d'un an.
CREATE OR REPLACE TABLE dim_date AS
WITH bounds AS (
    SELECT
        COALESCE(MIN(interview_date), CURRENT_DATE - INTERVAL 30 DAY) AS min_date,
        COALESCE(MAX(interview_date), CURRENT_DATE)                   AS max_date
    FROM silver_interview
),
calendar AS (
    SELECT UNNEST(GENERATE_SERIES(
        CAST((SELECT min_date FROM bounds) AS DATE) - INTERVAL 30 DAY,
        CAST((SELECT max_date FROM bounds) AS DATE) + INTERVAL 365 DAY,
        INTERVAL 1 DAY
    )) AS d
)
SELECT
    CAST(STRFTIME(d, '%Y%m%d') AS INTEGER)  AS date_key,
    CAST(d AS DATE)                         AS date,
    EXTRACT(year FROM d)                    AS year,
    EXTRACT(quarter FROM d)                 AS quarter,
    EXTRACT(month FROM d)                   AS month,
    STRFTIME(d, '%B')                       AS month_name,
    EXTRACT(week FROM d)                    AS week_of_year,
    EXTRACT(day FROM d)                     AS day_of_month,
    EXTRACT(dow FROM d)                     AS day_of_week,
    STRFTIME(d, '%A')                       AS day_name,
    EXTRACT(dow FROM d) IN (0, 6)           AS is_weekend,
    STRFTIME(d, '%Y-%m')                    AS year_month
FROM calendar;


-- Dimension enquete.
CREATE OR REPLACE TABLE dim_survey AS
SELECT
    survey_key,
    survey_code,
    survey_title,
    survey_status,
    target_sample,
    start_date,
    end_date,
    planned_duration_days,
    default_language,
    -- Liste lisible des modes actives, pour le filtrage dans Power BI.
    ARRAY_TO_STRING(
        CAST(collection_services_json AS VARCHAR[]), ' + '
    )                                        AS collection_services_label,
    LENGTH(collection_services_json)         AS collection_service_count,
    LENGTH(collection_services_json) > 1     AS is_multimode
FROM silver_survey;


-- Dimension service de collecte : referentiel fige, aligne sur le catalogue
-- expose a l'entree de la plateforme.
CREATE OR REPLACE TABLE dim_collection_service AS
SELECT * FROM (VALUES
    ('CAWI', 'Enquete en ligne',        'web',     FALSE, TRUE,  1, 12, 0.25),
    ('CAPI', 'Face a face',             'mobile',  TRUE,  FALSE, 5, 45, 0.85),
    ('CATI', 'Telephone',               'phone',   FALSE, FALSE, 3, 20, 0.45),
    ('PAPI', 'Papier',                  'paper',   FALSE, FALSE, 4, 40, 0.80),
    ('SMS',  'SMS / USSD',              'telecom', FALSE, TRUE,  1,  4, 0.18),
    ('MIXED','Dispositif multimode',    'multi',   TRUE,  FALSE, 4, 30, 0.70)
) AS t(
    collection_service,
    service_name,
    channel,
    supports_offline,
    is_self_administered,
    cost_index,
    typical_duration_minutes,
    typical_response_rate
);


-- Dimension enqueteur.
CREATE OR REPLACE TABLE dim_enumerator AS
SELECT
    enumerator_key,
    matricule,
    enumerator_name,
    enumerator_pseudonym,
    base_zone,
    supervisor_key,
    hired_at,
    daily_capacity,
    training_completed,
    enumerator_status,
    is_active,
    ARRAY_TO_STRING(CAST(certified_services_json AS VARCHAR[]), ', ') AS certified_services_label
FROM silver_enumerator;


-- Dimension question : permet d'analyser les reponses sans connaitre le
-- questionnaire, et de comparer plusieurs versions entre elles.
CREATE OR REPLACE TABLE dim_question AS
SELECT
    question_key,
    question_code,
    question_label,
    question_type,
    analysis_role,
    is_required,
    is_pii,
    section_key,
    section_code,
    section_label,
    section_order,
    question_order,
    questionnaire_key,
    questionnaire_version,
    survey_key,
    -- Ordre absolu dans le questionnaire, pour restituer la sequence exacte.
    ROW_NUMBER() OVER (
        PARTITION BY questionnaire_key ORDER BY section_order, question_order
    ) AS absolute_order
FROM silver_question;


-- Dimension geographie.
CREATE OR REPLACE TABLE dim_geography AS
SELECT
    geography_key,
    COALESCE(geo_level_1, 'Non renseigne') AS region,
    COALESCE(geo_level_2, 'Non renseigne') AS departement,
    COALESCE(geo_level_3, 'Non renseigne') AS commune
FROM silver_geography

UNION ALL

-- Membre inconnu : evite de perdre des faits sans geographie lors des jointures.
SELECT 'UNKNOWN', 'Non renseigne', 'Non renseigne', 'Non renseigne';


-- Dimension statut d'entretien, avec regroupement analytique.
CREATE OR REPLACE TABLE dim_interview_status AS
SELECT * FROM (VALUES
    ('in_progress', 'En cours',   'En cours',   FALSE),
    ('completed',   'Termine',    'Exploitable', TRUE),
    ('submitted',   'Transmis',   'Exploitable', TRUE),
    ('validated',   'Valide',     'Exploitable', TRUE),
    ('partial',     'Partiel',    'Incomplet',   FALSE),
    ('rejected',    'Rejete',     'Ecarte',      FALSE),
    ('refused',     'Refus',      'Ecarte',      FALSE)
) AS t(interview_status, status_label, status_group, is_usable);
