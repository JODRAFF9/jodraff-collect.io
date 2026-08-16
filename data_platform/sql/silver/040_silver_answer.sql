-- Silver : reponses en format long, typees et rattachees a leur question.
-- Les donnees personnelles sont neutralisees ici : au-dela de silver, plus
-- aucune couche ne manipule de texte libre identifiant.
CREATE OR REPLACE TABLE silver_answer AS
WITH ranked AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY id ORDER BY _ingested_at DESC) AS rn
    FROM bronze_answers
)
SELECT
    a.id                                   AS answer_key,
    a.interview_id                         AS interview_key,
    i.survey_key,
    i.collection_service,
    i.interview_date,
    i.enumerator_key,
    i.geo_level_1,
    i.geo_level_2,
    i.sampling_weight,
    a.question_code,
    a.question_type,
    a.repeat_index,

    q.question_key,
    q.question_label,
    q.section_code,
    q.section_label,
    q.analysis_role,
    q.is_pii,

    -- Valeur textuelle : masquee si la question porte une donnee personnelle.
    CASE WHEN COALESCE(q.is_pii, FALSE) THEN NULL ELSE a.value_text END AS value_text,
    a.value_number,
    CAST(a.value_date AS TIMESTAMP)        AS value_date,
    TRY_CAST(a.value_json AS JSON)         AS value_json,
    a.is_other,

    -- Libelle de modalite pour les questions fermees a reponse unique.
    c.choice_label,
    c.choice_score,

    -- Nombre de modalites cochees pour les questions a choix multiple.
    CASE
        WHEN a.question_type = 'multi_choice'
        THEN LENGTH(COALESCE(TRY_CAST(a.value_json AS JSON), '[]'::JSON))
    END                                    AS selected_count,

    -- Une reponse est consideree renseignee si au moins un champ typé est rempli.
    (a.value_text IS NOT NULL OR a.value_number IS NOT NULL
     OR a.value_date IS NOT NULL OR a.value_json IS NOT NULL) AS is_answered,

    CAST(a.answered_at AS TIMESTAMP)       AS answered_at
FROM ranked a
JOIN silver_interview i ON i.interview_key = a.interview_id
LEFT JOIN silver_question q
       ON q.question_code = a.question_code
      AND q.questionnaire_key = i.questionnaire_key
LEFT JOIN silver_choice c
       ON c.choice_list_id = q.choice_list_id
      AND c.choice_code = a.value_text
      AND a.question_type = 'single_choice'
WHERE a.rn = 1;
