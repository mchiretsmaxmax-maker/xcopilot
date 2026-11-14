# ER-диаграмма

> Генерируется командой `make db-docs` из моделей (`xcopilot.db.docs`). Не редактировать вручную.

```mermaid
erDiagram
    account_members {
        uuid id PK
        uuid account_id FK
        uuid user_id FK
        varchar role
        timestamp_with_time_zone created_at
    }
    account_metric_snapshots {
        uuid id PK
        uuid account_id FK
        date date
        integer followers
        integer following
        integer posts_count
        timestamp_with_time_zone created_at
    }
    accounts {
        uuid id PK
        varchar kind
        varchar handle
        varchar x_user_id
        varchar niche
        varchar lang
        varchar tz
        varchar x_read_mode
        jsonb settings
        varchar status
        timestamp_with_time_zone created_at
        timestamp_with_time_zone updated_at
    }
    authors {
        uuid id PK
        uuid account_id FK
        varchar handle
        varchar x_user_id
        varchar display_name
        boolean is_self
        integer followers
        timestamp_with_time_zone followers_observed_at
        varchar followers_origin
        varchar niche
        smallint trust
        varchar note
        timestamp_with_time_zone created_at
        timestamp_with_time_zone updated_at
    }
    bot_sessions {
        uuid id PK
        uuid account_id FK
        uuid user_id FK
        varchar state
        jsonb data
        timestamp_with_time_zone updated_at
    }
    budgets {
        uuid id PK
        uuid account_id FK
        varchar scope
        varchar period
        bigint limit_micro_usd
        boolean hard
        timestamp_with_time_zone created_at
        timestamp_with_time_zone updated_at
    }
    chunks {
        uuid id PK
        uuid account_id FK
        uuid document_id FK
        integer idx
        varchar text
        text tags
        varchar lang
        tsvector tsv
        timestamp_with_time_zone created_at
    }
    competitors {
        uuid id PK
        uuid account_id FK
        varchar handle
        varchar x_user_id
        integer priority
        boolean active
        varchar since_id
        timestamp_with_time_zone created_at
    }
    documents {
        uuid id PK
        uuid account_id FK
        uuid source_id FK
        varchar text
        varchar text_clean
        integer token_count
        timestamp_with_time_zone created_at
    }
    drafts {
        uuid id PK
        uuid account_id FK
        uuid run_id FK
        integer variant_no
        integer revision
        uuid parent_draft_id FK
        varchar format
        varchar text
        jsonb parts
        varchar reply_to_url
        varchar status
        jsonb lint_report
        jsonb judge_verdict
        timestamp_with_time_zone created_at
        timestamp_with_time_zone updated_at
        uuid probation_item_id FK
    }
    embedding_models {
        uuid id PK
        varchar name
        integer dim
        boolean active
        timestamp_with_time_zone created_at
        numeric dup_threshold
        numeric related_threshold
    }
    embeddings {
        uuid id PK
        uuid account_id FK
        uuid chunk_id FK
        uuid model_id FK
        vector_384 vec
        timestamp_with_time_zone created_at
    }
    events {
        uuid id PK
        uuid account_id FK
        timestamp_with_time_zone ts PK
        varchar type
        varchar actor
        uuid user_id
        varchar entity_type
        uuid entity_id
        jsonb props
    }
    facts {
        uuid id PK
        uuid account_id FK
        varchar statement
        varchar value
        varchar unit
        varchar source_url
        timestamp_with_time_zone observed_at
        timestamp_with_time_zone expires_at
        timestamp_with_time_zone created_at
    }
    generation_runs {
        uuid id PK
        uuid account_id FK
        uuid topic_id FK
        varchar kind
        jsonb context_pack
        integer passport_version
        uuid prompt_id FK
        varchar status
        varchar error
        timestamp_with_time_zone created_at
        timestamp_with_time_zone finished_at
        uuid knowledge_pack_id FK
    }
    insights {
        uuid id PK
        uuid account_id FK
        varchar kind
        jsonb body
        jsonb source_refs
        double_precision confidence
        timestamp_with_time_zone valid_until
        timestamp_with_time_zone created_at
    }
    jobs {
        uuid id PK
        uuid account_id FK
        varchar kind
        jsonb payload
        timestamp_with_time_zone run_at
        varchar status
        integer attempts
        integer max_attempts
        varchar idempotency_key
        varchar locked_by
        timestamp_with_time_zone locked_at
        varchar last_error
        timestamp_with_time_zone created_at
        timestamp_with_time_zone updated_at
        timestamp_with_time_zone finished_at
    }
    knowledge_applications {
        uuid id PK
        uuid account_id FK
        uuid pack_id FK
        uuid item_id FK
        uuid draft_id FK
        varchar slot
        boolean applied
        numeric applied_score
        varchar checked_by
        timestamp_with_time_zone checked_at
        timestamp_with_time_zone created_at
    }
    knowledge_embeddings {
        uuid item_id PK, FK
        uuid account_id FK
        uuid model_id PK, FK
        integer version
        vector_384 vec
    }
    knowledge_evidence {
        uuid id PK
        uuid account_id FK
        uuid item_id FK
        uuid source_id FK
        uuid draft_id FK
        varchar dimension
        varchar basis
        smallint polarity
        numeric detector_score
        varchar outcome_tier
        varchar target_metric
        varchar format
        uuid author_id FK
        numeric w_source
        numeric w_scope
        numeric w_magnitude
        numeric w_author
        numeric weight
        timestamp_with_time_zone observed_at
        timestamp_with_time_zone created_at
        timestamp_with_time_zone revoked_at
        varchar revoked_reason
    }
    knowledge_item_scopes {
        uuid item_id PK, FK
        uuid account_id FK
        varchar facet PK
        varchar value PK
    }
    knowledge_item_versions {
        uuid item_id PK, FK
        uuid account_id FK
        integer version PK
        varchar statement
        varchar guidance
        varchar skeleton
        jsonb detector
        varchar kind
        varchar layer
        varchar element
        varchar target_metric
        varchar changed_by
        uuid user_id FK
        varchar reason
        jsonb provenance
        timestamp_with_time_zone created_at
    }
    knowledge_items {
        uuid id PK
        uuid account_id FK
        varchar kind
        varchar layer
        varchar element
        varchar statement
        varchar guidance
        varchar skeleton
        varchar target_metric
        jsonb detector
        char_1 risk_class
        varchar status
        varchar owner_state
        varchar origin
        uuid parent_item_id
        uuid merged_into FK
        integer version
        numeric eff_alpha
        numeric eff_beta
        numeric fit_alpha
        numeric fit_beta
        numeric eff_conf
        numeric fit_conf
        numeric quality
        integer evidence_n
        integer independent_authors
        integer own_support
        integer own_contra
        integer occurrences
        timestamp_with_time_zone last_evidence_at
        timestamp_with_time_zone scores_updated_at
        timestamp_with_time_zone probation_started_at
        timestamp_with_time_zone created_at
        timestamp_with_time_zone updated_at
    }
    knowledge_links {
        uuid id PK
        uuid account_id FK
        uuid from_item_id FK
        uuid to_item_id FK
        varchar relation
        numeric confidence
        varchar decided_by
        varchar status
        varchar resolution
        timestamp_with_time_zone created_at
        timestamp_with_time_zone resolved_at
    }
    knowledge_packs {
        uuid id PK
        uuid account_id FK
        varchar consumer
        jsonb task
        integer schema_version
        integer taxonomy_version
        jsonb items
        integer token_count
        timestamp_with_time_zone created_at
    }
    knowledge_snapshots {
        date snapshot_date PK
        uuid account_id PK, FK
        uuid item_id PK
        varchar status
        varchar owner_state
        integer version
        varchar layer
        numeric eff_alpha
        numeric eff_beta
        numeric fit_alpha
        numeric fit_beta
        numeric eff_conf
        numeric fit_conf
        numeric quality
        integer evidence_n
        integer own_support
        integer own_contra
    }
    knowledge_transitions {
        uuid id PK
        uuid account_id FK
        uuid item_id FK
        timestamp_with_time_zone ts
        varchar from_status
        varchar to_status
        varchar actor
        uuid user_id FK
        varchar rule
        varchar reason
        jsonb payload
        timestamp_with_time_zone reverted_at
        uuid reverted_by FK
    }
    llm_calls {
        uuid id PK
        uuid account_id FK
        timestamp_with_time_zone ts PK
        varchar role
        varchar provider
        varchar model
        uuid prompt_id FK
        varchar purpose
        integer input_tokens
        integer cached_tokens
        integer output_tokens
        bigint cost_micro_usd
        integer latency_ms
        varchar status
        varchar error
        varchar entity_type
        uuid entity_id
    }
    material_analyses {
        uuid id PK
        uuid account_id FK
        uuid source_id FK
        varchar analyzer_version
        integer taxonomy_version
        varchar model
        uuid llm_call_id
        boolean is_current
        varchar format
        varchar archetype
        varchar lang
        varchar hook_type
        varchar hook_text
        varchar structure
        jsonb structure_outline
        text tone
        text devices
        varchar closer
        text human_markers
        text ai_markers
        jsonb features
        jsonb why_hypotheses
        jsonb declared_claims
        jsonb proposed_terms
        text quality_flags
        timestamp_with_time_zone created_at
    }
    material_outcomes {
        uuid id PK
        uuid account_id FK
        uuid source_id FK
        integer method_version
        varchar basis
        varchar signal_origin
        numeric_3_2 reliability
        numeric engagement_rate
        numeric reply_rate
        numeric save_rate
        numeric reach_ratio
        numeric pct_reach
        numeric pct_engagement
        numeric pct_replies
        numeric pct_saves
        varchar tier_reach
        varchar tier_engagement
        varchar tier_replies
        varchar tier_saves
        boolean is_current
        timestamp_with_time_zone computed_at
    }
    material_signals {
        uuid id PK
        uuid account_id FK
        uuid source_id FK
        timestamp_with_time_zone observed_at
        varchar origin
        numeric post_age_hours
        bigint views
        integer likes
        integer replies
        integer reposts
        integer quotes
        integer bookmarks
        integer author_followers
        varchar owner_label
        numeric_3_2 read_confidence
        uuid confirmed_by FK
        jsonb raw
    }
    metric_definitions {
        varchar key PK
        integer version
        varchar title
        varchar description
        varchar unit
        varchar grain
        jsonb dimensions
        varchar formula_sql
        varchar formula_hash
        boolean active
        timestamp_with_time_zone created_at
        timestamp_with_time_zone updated_at
    }
    metric_values {
        uuid account_id PK, FK
        varchar metric_key PK, FK
        varchar grain PK
        date period_start PK
        jsonb dims
        varchar dims_hash PK
        numeric value
        integer sample_size
        integer definition_version
        timestamp_with_time_zone computed_at
    }
    post_metric_snapshots {
        uuid id PK
        uuid account_id FK
        varchar x_post_id
        timestamp_with_time_zone ts PK
        integer age_minutes
        bigint impressions
        integer likes
        integer replies
        integer reposts
        integer quotes
        integer bookmarks
        integer url_clicks
        integer profile_clicks
        jsonb raw
    }
    prompts {
        uuid id PK
        varchar key
        integer version
        varchar role
        varchar template
        jsonb output_schema
        varchar content_hash
        timestamp_with_time_zone created_at
    }
    publications {
        uuid id PK
        uuid account_id FK
        uuid draft_id FK
        varchar x_post_id
        varchar url
        timestamp_with_time_zone published_at
        varchar matched_by
        double_precision match_score
        varchar final_text
        timestamp_with_time_zone created_at
    }
    reviews {
        uuid id PK
        uuid account_id FK
        uuid draft_id FK
        uuid user_id FK
        varchar action
        varchar reason_code
        varchar text_before
        varchar text_after
        double_precision edit_distance
        timestamp_with_time_zone created_at
    }
    schedules {
        uuid id PK
        uuid account_id FK
        varchar kind
        time_without_time_zone at_local_time
        smallint weekday
        boolean enabled
        jsonb payload
        timestamp_with_time_zone last_run_at
        timestamp_with_time_zone created_at
    }
    secrets {
        uuid id PK
        uuid account_id FK
        varchar kind
        bytea ciphertext
        integer key_version
        timestamp_with_time_zone created_at
        timestamp_with_time_zone updated_at
    }
    segments {
        uuid id PK
        uuid account_id FK
        varchar key
        varchar title
        jsonb filter
        integer position
        boolean is_default
        timestamp_with_time_zone created_at
    }
    sources {
        uuid id PK
        uuid account_id FK
        varchar kind
        varchar origin
        varchar external_id
        varchar url
        varchar author_handle
        varchar content_hash
        varchar status
        jsonb meta
        timestamp_with_time_zone created_at
        varchar stance
        uuid author_id FK
        uuid parent_source_id FK
        uuid thread_root_id FK
        timestamp_with_time_zone posted_at
        varchar lang
        varchar owner_note
        varchar analysis_status
        varchar quarantine_reason
        timestamp_with_time_zone removed_at
    }
    style_passports {
        uuid id PK
        uuid account_id FK
        integer version
        varchar status
        jsonb body
        jsonb hard_rules
        jsonb source_refs
        varchar created_by
        timestamp_with_time_zone created_at
    }
    taxonomy_terms {
        uuid id PK
        uuid account_id FK
        varchar facet
        varchar key
        varchar title
        varchar description
        varchar status
        varchar replaced_by
        integer since_version
        varchar created_by
        timestamp_with_time_zone created_at
    }
    topics {
        uuid id PK
        uuid account_id FK
        varchar title
        varchar angle
        varchar origin
        varchar status
        timestamp_with_time_zone planned_for
        vector_384 dedupe_vec
        timestamp_with_time_zone created_at
    }
    users {
        uuid id PK
        bigint tg_user_id
        varchar name
        boolean is_admin
        timestamp_with_time_zone created_at
    }
    x_api_calls {
        uuid id PK
        uuid account_id FK
        timestamp_with_time_zone ts PK
        varchar endpoint
        integer resources
        bigint cost_micro_usd
        date utc_day
        integer http_status
        varchar status
        varchar budget_scope
    }
    accounts ||--o{ account_members : "account_id"
    accounts ||--o{ account_metric_snapshots : "account_id"
    accounts ||--o{ authors : "account_id"
    accounts ||--o{ bot_sessions : "account_id"
    accounts ||--o{ budgets : "account_id"
    accounts ||--o{ chunks : "account_id"
    accounts ||--o{ competitors : "account_id"
    accounts ||--o{ documents : "account_id"
    accounts ||--o{ drafts : "account_id"
    accounts ||--o{ embeddings : "account_id"
    accounts ||--o{ events : "account_id"
    accounts ||--o{ facts : "account_id"
    accounts ||--o{ generation_runs : "account_id"
    accounts ||--o{ insights : "account_id"
    accounts ||--o{ jobs : "account_id"
    accounts ||--o{ knowledge_applications : "account_id"
    accounts ||--o{ knowledge_embeddings : "account_id"
    accounts ||--o{ knowledge_evidence : "account_id"
    accounts ||--o{ knowledge_item_scopes : "account_id"
    accounts ||--o{ knowledge_item_versions : "account_id"
    accounts ||--o{ knowledge_items : "account_id"
    accounts ||--o{ knowledge_links : "account_id"
    accounts ||--o{ knowledge_packs : "account_id"
    accounts ||--o{ knowledge_snapshots : "account_id"
    accounts ||--o{ knowledge_transitions : "account_id"
    accounts ||--o{ llm_calls : "account_id"
    accounts ||--o{ material_analyses : "account_id"
    accounts ||--o{ material_outcomes : "account_id"
    accounts ||--o{ material_signals : "account_id"
    accounts ||--o{ metric_values : "account_id"
    accounts ||--o{ post_metric_snapshots : "account_id"
    accounts ||--o{ publications : "account_id"
    accounts ||--o{ reviews : "account_id"
    accounts ||--o{ schedules : "account_id"
    accounts ||--o{ secrets : "account_id"
    accounts ||--o{ segments : "account_id"
    accounts ||--o{ sources : "account_id"
    accounts ||--o{ style_passports : "account_id"
    accounts ||--o{ taxonomy_terms : "account_id"
    accounts ||--o{ topics : "account_id"
    accounts ||--o{ x_api_calls : "account_id"
    authors ||--o{ knowledge_evidence : "account_id"
    authors ||--o{ knowledge_evidence : "author_id"
    authors ||--o{ sources : "account_id"
    authors ||--o{ sources : "author_id"
    chunks ||--o{ embeddings : "chunk_id"
    documents ||--o{ chunks : "document_id"
    drafts ||--o{ drafts : "parent_draft_id"
    drafts ||--o{ knowledge_applications : "draft_id"
    drafts ||--o{ knowledge_evidence : "draft_id"
    drafts ||--o{ publications : "draft_id"
    drafts ||--o{ reviews : "draft_id"
    embedding_models ||--o{ embeddings : "model_id"
    embedding_models ||--o{ knowledge_embeddings : "model_id"
    generation_runs ||--o{ drafts : "run_id"
    knowledge_items ||--o{ drafts : "probation_item_id"
    knowledge_items ||--o{ knowledge_applications : "account_id"
    knowledge_items ||--o{ knowledge_applications : "item_id"
    knowledge_items ||--o{ knowledge_embeddings : "account_id"
    knowledge_items ||--o{ knowledge_embeddings : "item_id"
    knowledge_items ||--o{ knowledge_evidence : "account_id"
    knowledge_items ||--o{ knowledge_evidence : "item_id"
    knowledge_items ||--o{ knowledge_item_scopes : "account_id"
    knowledge_items ||--o{ knowledge_item_scopes : "item_id"
    knowledge_items ||--o{ knowledge_item_versions : "account_id"
    knowledge_items ||--o{ knowledge_item_versions : "item_id"
    knowledge_items ||--o{ knowledge_items : "account_id"
    knowledge_items ||--o{ knowledge_items : "merged_into"
    knowledge_items ||--o{ knowledge_links : "account_id"
    knowledge_items ||--o{ knowledge_links : "from_item_id"
    knowledge_items ||--o{ knowledge_links : "to_item_id"
    knowledge_items ||--o{ knowledge_transitions : "account_id"
    knowledge_items ||--o{ knowledge_transitions : "item_id"
    knowledge_packs ||--o{ generation_runs : "knowledge_pack_id"
    knowledge_packs ||--o{ knowledge_applications : "account_id"
    knowledge_packs ||--o{ knowledge_applications : "pack_id"
    metric_definitions ||--o{ metric_values : "metric_key"
    prompts ||--o{ generation_runs : "prompt_id"
    prompts ||--o{ llm_calls : "prompt_id"
    sources ||--o{ documents : "source_id"
    sources ||--o{ knowledge_evidence : "account_id"
    sources ||--o{ knowledge_evidence : "source_id"
    sources ||--o{ material_analyses : "account_id"
    sources ||--o{ material_analyses : "source_id"
    sources ||--o{ material_outcomes : "account_id"
    sources ||--o{ material_outcomes : "source_id"
    sources ||--o{ material_signals : "account_id"
    sources ||--o{ material_signals : "source_id"
    sources ||--o{ sources : "account_id"
    sources ||--o{ sources : "parent_source_id"
    sources ||--o{ sources : "thread_root_id"
    topics ||--o{ generation_runs : "topic_id"
    users ||--o{ account_members : "user_id"
    users ||--o{ bot_sessions : "user_id"
    users ||--o{ knowledge_item_versions : "user_id"
    users ||--o{ knowledge_transitions : "reverted_by"
    users ||--o{ knowledge_transitions : "user_id"
    users ||--o{ material_signals : "confirmed_by"
    users ||--o{ reviews : "user_id"
```
