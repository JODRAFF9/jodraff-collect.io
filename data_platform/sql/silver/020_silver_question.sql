-- Silver : metadonnees des questions, enrichies de leur section et de leur enquete.
-- C'est le referentiel qui rend les reponses interpretables en aval.
CREATE OR REPLACE TABLE silver_question AS
WITH q AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY id ORDER BY _ingested_at DESC) AS rn
    FROM bronze_questions
),
s AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY id ORDER BY _ingested_at DESC) AS rn
    FROM bronze_sections
),
qn AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY id ORDER BY _ingested_at DESC) AS rn
    FROM bronze_questionnaires
)
SELECT
    q.id                                                    AS question_key,
    q.code                                                  AS question_code,
    q.type                                                  AS question_type,
    -- Le libelle est stocke en JSON multilingue : on expose le francais et un repli.
    COALESCE(
        TRY_CAST(q.label AS JSON) ->> '$.fr',
        TRY_CAST(q.label AS JSON) ->> '$.en',
        q.code
    )                                                       AS question_label,
    q.order_index                                           AS question_order,
    q.is_required,
    q.relevance                                             AS relevance_rule,
    q.min_value,
    q.max_value,
    q.choice_list_id,
    q.is_pii,
    COALESCE(q.analysis_role, CASE
        WHEN q.type IN ('integer', 'decimal', 'scale') THEN 'mesure'
        WHEN q.type IN ('single_choice', 'multi_choice') THEN 'dimension'
        ELSE 'attribut'
    END)                                                    AS analysis_role,
    TRY_CAST(q.only_services AS JSON)                       AS only_services_json,
    s.id                                                    AS section_key,
    s.code                                                  AS section_code,
    COALESCE(TRY_CAST(s.label AS JSON) ->> '$.fr', s.code)  AS section_label,
    s.order_index                                           AS section_order,
    s.is_repeatable                                         AS section_is_repeatable,
    qn.id                                                   AS questionnaire_key,
    qn.survey_id                                            AS survey_key,
    qn.version                                              AS questionnaire_version,
    qn.schema_hash
FROM q
JOIN s  ON s.id = q.section_id AND s.rn = 1
JOIN qn ON qn.id = s.questionnaire_id AND qn.rn = 1
WHERE q.rn = 1;


-- Silver : modalites de reponse, pour restituer des libelles et non des codes.
CREATE OR REPLACE TABLE silver_choice AS
WITH ranked AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY id ORDER BY _ingested_at DESC) AS rn
    FROM bronze_choices
)
SELECT
    id                                                     AS choice_key,
    choice_list_id,
    code                                                   AS choice_code,
    COALESCE(TRY_CAST(label AS JSON) ->> '$.fr', code)     AS choice_label,
    order_index                                            AS choice_order,
    score                                                  AS choice_score
FROM ranked
WHERE rn = 1;
