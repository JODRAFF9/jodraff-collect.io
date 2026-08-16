/* ==========================================================================
   Couche gold — DDL cible pour Azure SQL Database et Microsoft Fabric
   Warehouse.

   En local, ces tables sont materialisees par DuckDB a partir des modeles
   SQL de data_platform/sql/gold. Ce fichier est la definition equivalente
   pour un deploiement cloud, ou le schema doit exister avant le chargement
   et etre gere par migration versionnee.

   Conventions :
   - cles de substitution en CHAR(32) : identifiants hexadecimaux du systeme
     operationnel, de longueur fixe, donc indexables efficacement ;
   - pas de contrainte de cle etrangere sur les faits : le controle
     d'integrite est fait par les tests du pipeline, ce qui evite de bloquer
     un chargement massif sur une dimension arrivee en retard ;
   - index columnstore groupe sur les faits, alignement sur le moteur
     analytique de Fabric et de Synapse.
   ========================================================================== */

IF SCHEMA_ID('gold') IS NULL
    EXEC('CREATE SCHEMA gold');
GO

/* --------------------------------------------------------------------------
   Dimensions
   -------------------------------------------------------------------------- */

DROP TABLE IF EXISTS gold.dim_date;
CREATE TABLE gold.dim_date (
    date_key        INT           NOT NULL,
    [date]          DATE          NOT NULL,
    [year]          SMALLINT      NOT NULL,
    [quarter]       TINYINT       NOT NULL,
    [month]         TINYINT       NOT NULL,
    month_name      NVARCHAR(20)  NULL,
    week_of_year    TINYINT       NULL,
    day_of_month    TINYINT       NULL,
    day_of_week     TINYINT       NULL,
    day_name        NVARCHAR(20)  NULL,
    is_weekend      BIT           NOT NULL,
    year_month      CHAR(7)       NULL,
    CONSTRAINT pk_dim_date PRIMARY KEY NONCLUSTERED (date_key) NOT ENFORCED
);
GO

DROP TABLE IF EXISTS gold.dim_survey;
CREATE TABLE gold.dim_survey (
    survey_key                  CHAR(32)       NOT NULL,
    survey_code                 NVARCHAR(48)   NOT NULL,
    survey_title                NVARCHAR(300)  NOT NULL,
    survey_status               NVARCHAR(24)   NOT NULL,
    target_sample               INT            NULL,
    start_date                  DATE           NULL,
    end_date                    DATE           NULL,
    planned_duration_days       INT            NULL,
    default_language            NVARCHAR(8)    NULL,
    collection_services_label   NVARCHAR(120)  NULL,
    collection_service_count    TINYINT        NULL,
    is_multimode                BIT            NULL,
    CONSTRAINT pk_dim_survey PRIMARY KEY NONCLUSTERED (survey_key) NOT ENFORCED
);
GO

DROP TABLE IF EXISTS gold.dim_collection_service;
CREATE TABLE gold.dim_collection_service (
    collection_service          NVARCHAR(16)  NOT NULL,
    service_name                NVARCHAR(80)  NOT NULL,
    channel                     NVARCHAR(16)  NOT NULL,
    supports_offline            BIT           NOT NULL,
    is_self_administered        BIT           NOT NULL,
    cost_index                  TINYINT       NOT NULL,
    typical_duration_minutes    SMALLINT      NOT NULL,
    typical_response_rate       DECIMAL(5, 4) NOT NULL,
    CONSTRAINT pk_dim_collection_service
        PRIMARY KEY NONCLUSTERED (collection_service) NOT ENFORCED
);
GO

DROP TABLE IF EXISTS gold.dim_enumerator;
CREATE TABLE gold.dim_enumerator (
    enumerator_key              CHAR(32)       NOT NULL,
    matricule                   NVARCHAR(32)   NULL,
    enumerator_name             NVARCHAR(200)  NULL,
    enumerator_pseudonym        NVARCHAR(48)   NULL,
    base_zone                   NVARCHAR(120)  NULL,
    supervisor_key              CHAR(32)       NULL,
    hired_at                    DATE           NULL,
    daily_capacity              SMALLINT       NULL,
    training_completed          BIT            NULL,
    enumerator_status           NVARCHAR(32)   NULL,
    is_active                   BIT            NULL,
    certified_services_label    NVARCHAR(120)  NULL,
    CONSTRAINT pk_dim_enumerator PRIMARY KEY NONCLUSTERED (enumerator_key) NOT ENFORCED
);
GO

DROP TABLE IF EXISTS gold.dim_question;
CREATE TABLE gold.dim_question (
    question_key            CHAR(32)       NOT NULL,
    question_code           NVARCHAR(48)   NOT NULL,
    question_label          NVARCHAR(1000) NULL,
    question_type           NVARCHAR(24)   NOT NULL,
    analysis_role           NVARCHAR(32)   NULL,
    is_required             BIT            NULL,
    is_pii                  BIT            NULL,
    section_key             CHAR(32)       NULL,
    section_code            NVARCHAR(48)   NULL,
    section_label           NVARCHAR(300)  NULL,
    section_order           INT            NULL,
    question_order          INT            NULL,
    questionnaire_key       CHAR(32)       NOT NULL,
    questionnaire_version   INT            NULL,
    schema_hash             NVARCHAR(64)   NULL,
    survey_key              CHAR(32)       NOT NULL,
    absolute_order          INT            NULL,
    CONSTRAINT pk_dim_question PRIMARY KEY NONCLUSTERED (question_key) NOT ENFORCED
);
GO

DROP TABLE IF EXISTS gold.dim_geography;
CREATE TABLE gold.dim_geography (
    geography_key   NVARCHAR(32)   NOT NULL,
    region          NVARCHAR(120)  NOT NULL,
    departement     NVARCHAR(120)  NOT NULL,
    commune         NVARCHAR(120)  NOT NULL,
    CONSTRAINT pk_dim_geography PRIMARY KEY NONCLUSTERED (geography_key) NOT ENFORCED
);
GO

DROP TABLE IF EXISTS gold.dim_interview_status;
CREATE TABLE gold.dim_interview_status (
    interview_status    NVARCHAR(24)  NOT NULL,
    status_label        NVARCHAR(40)  NOT NULL,
    status_group        NVARCHAR(40)  NOT NULL,
    is_usable           BIT           NOT NULL,
    CONSTRAINT pk_dim_interview_status
        PRIMARY KEY NONCLUSTERED (interview_status) NOT ENFORCED
);
GO

/* --------------------------------------------------------------------------
   Faits
   -------------------------------------------------------------------------- */

DROP TABLE IF EXISTS gold.fact_interview;
CREATE TABLE gold.fact_interview (
    interview_key           CHAR(32)        NOT NULL,
    survey_key              CHAR(32)        NOT NULL,
    questionnaire_key       CHAR(32)        NOT NULL,
    enumerator_key          CHAR(32)        NULL,
    assignment_key          CHAR(32)        NULL,
    collection_service      NVARCHAR(16)    NOT NULL,
    interview_status        NVARCHAR(24)    NOT NULL,
    geography_key           NVARCHAR(32)    NULL,
    date_key                INT             NULL,
    interview_date          DATE            NULL,
    interview_hour          TINYINT         NULL,
    stratum                 NVARCHAR(120)   NULL,

    interview_count         INT             NOT NULL,
    usable_count            INT             NOT NULL,
    validated_count         INT             NOT NULL,
    rejected_count          INT             NOT NULL,
    partial_count           INT             NOT NULL,
    duration_seconds        INT             NULL,
    duration_minutes        DECIMAL(10, 2)  NULL,
    quality_score           DECIMAL(5, 3)   NULL,
    quality_flag_count      SMALLINT        NULL,
    sampling_weight         DECIMAL(12, 6)  NOT NULL,
    weighted_usable         DECIMAL(12, 6)  NOT NULL,

    event_count             INT             NULL,
    change_count            INT             NULL,
    correction_ratio        DECIMAL(10, 4)  NULL,
    questions_touched       INT             NULL,

    assignment_attempts     SMALLINT        NULL,
    device_id               NVARCHAR(64)    NULL,
    app_version             NVARCHAR(32)    NULL,
    validated_at            DATETIME2(0)    NULL
);
GO
CREATE CLUSTERED COLUMNSTORE INDEX cci_fact_interview ON gold.fact_interview;
GO

DROP TABLE IF EXISTS gold.fact_answer;
CREATE TABLE gold.fact_answer (
    answer_key          CHAR(32)        NOT NULL,
    interview_key       CHAR(32)        NOT NULL,
    question_key        CHAR(32)        NULL,
    survey_key          CHAR(32)        NOT NULL,
    enumerator_key      CHAR(32)        NULL,
    collection_service  NVARCHAR(16)    NOT NULL,
    question_code       NVARCHAR(48)    NOT NULL,
    question_type       NVARCHAR(24)    NOT NULL,
    repeat_index        SMALLINT        NOT NULL,
    date_key            INT             NULL,
    geography_key       NVARCHAR(32)    NULL,

    -- Les colonnes de valeur sont anonymisees en amont : aucune reponse
    -- portant une donnee personnelle n'atteint cette table en clair.
    value_text          NVARCHAR(4000)  NULL,
    value_number        FLOAT           NULL,
    value_date          DATETIME2(0)    NULL,
    value_label         NVARCHAR(1000)  NULL,
    choice_score        FLOAT           NULL,
    selected_count      SMALLINT        NULL,

    answer_count        INT             NOT NULL,
    answered_count      INT             NOT NULL,
    missing_count       INT             NOT NULL,
    sampling_weight     DECIMAL(12, 6)  NOT NULL,
    weighted_answered   DECIMAL(12, 6)  NOT NULL,
    weighted_value      FLOAT           NULL
);
GO
CREATE CLUSTERED COLUMNSTORE INDEX cci_fact_answer ON gold.fact_answer;
GO

DROP TABLE IF EXISTS gold.fact_fieldwork_daily;
CREATE TABLE gold.fact_fieldwork_daily (
    enumerator_key          CHAR(32)        NOT NULL,
    collection_service      NVARCHAR(16)    NOT NULL,
    date_key                INT             NOT NULL,
    interview_date          DATE            NOT NULL,
    base_zone               NVARCHAR(120)   NULL,
    supervisor_key          CHAR(32)        NULL,
    interviews_started      INT             NOT NULL,
    interviews_usable       INT             NOT NULL,
    interviews_rejected     INT             NOT NULL,
    interviews_partial      INT             NOT NULL,
    avg_duration_seconds    DECIMAL(12, 2)  NULL,
    total_duration_seconds  BIGINT          NULL,
    avg_quality_score       DECIMAL(5, 3)   NULL,
    daily_capacity          SMALLINT        NULL,
    capacity_utilisation    DECIMAL(10, 4)  NULL,
    field_span_hours        DECIMAL(10, 2)  NULL
);
GO
CREATE CLUSTERED COLUMNSTORE INDEX cci_fact_fieldwork_daily ON gold.fact_fieldwork_daily;
GO

DROP TABLE IF EXISTS gold.fact_sample_coverage;
CREATE TABLE gold.fact_sample_coverage (
    survey_key              CHAR(32)        NOT NULL,
    collection_service      NVARCHAR(16)    NULL,
    enumerator_key          CHAR(32)        NULL,
    geography_key           NVARCHAR(32)    NULL,
    stratum                 NVARCHAR(120)   NULL,
    assignments_total       INT             NOT NULL,
    assignments_done        INT             NOT NULL,
    assignments_open        INT             NOT NULL,
    assignments_overdue     INT             NOT NULL,
    attempts_total          INT             NULL,
    weight_total            DECIMAL(14, 6)  NULL,
    weight_covered          DECIMAL(14, 6)  NULL
);
GO

DROP TABLE IF EXISTS gold.fact_quota;
CREATE TABLE gold.fact_quota (
    quota_key               CHAR(32)        NOT NULL,
    survey_key              CHAR(32)        NOT NULL,
    quota_label             NVARCHAR(200)   NULL,
    dimensions_json         NVARCHAR(2000)  NULL,
    quota_target            INT             NOT NULL,
    quota_achieved          INT             NOT NULL,
    quota_remaining         INT             NOT NULL,
    quota_completion_rate   DECIMAL(10, 4)  NULL,
    is_full                 BIT             NOT NULL,
    is_blocking             BIT             NOT NULL
);
GO
