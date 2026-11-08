# Databricks notebook source
# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE workspace.hl7_silver.adt_events AS
# MAGIC SELECT
# MAGIC   value:identifier.value                                     AS message_id,
# MAGIC   to_timestamp(value:timestamp)                              AS message_time,
# MAGIC   value:entry[1].resource.status                             AS encounter_status,
# MAGIC   value:entry[0].resource.id                                 AS mrn,
# MAGIC   value:entry[0].resource.name[0].given[0]                   AS first_name,
# MAGIC   value:entry[0].resource.name[0].family                     AS last_name,
# MAGIC   to_date(value:entry[0].resource.birthDate)                 AS birth_date,
# MAGIC   value:entry[0].resource.gender                             AS gender,
# MAGIC   value:entry[0].resource.address[0].postalCode              AS zip_code,
# MAGIC   value:entry[1].resource.id                                 AS visit_id,
# MAGIC   value:entry[1].resource.class.code                         AS encounter_class,
# MAGIC   value:entry[1].resource.priority.coding[0].code            AS priority,
# MAGIC   value:entry[1].resource.location[0].location.display       AS location,
# MAGIC   value:entry[1].resource.participant[0].individual.display  AS attending,
# MAGIC   to_timestamp(value:entry[1].resource.period.start)         AS admit_time,
# MAGIC   to_timestamp(value:entry[1].resource.period.end)           AS discharge_time,
# MAGIC   source_file,
# MAGIC   ingest_time
# MAGIC FROM workspace.hl7_bronze.fhir_bundles_raw;

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE workspace.hl7_silver.adt_rejects AS
# MAGIC SELECT
# MAGIC   *,
# MAGIC   CASE
# MAGIC     WHEN mrn IS NULL        THEN 'missing MRN'
# MAGIC     WHEN visit_id IS NULL   THEN 'missing visit ID'
# MAGIC     WHEN admit_time IS NULL THEN 'missing admit time'
# MAGIC   END AS reason
# MAGIC FROM workspace.hl7_silver.adt_events
# MAGIC WHERE mrn IS NULL OR visit_id IS NULL OR admit_time IS NULL;

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE workspace.hl7_silver.adt_events_clean AS
# MAGIC WITH numbered AS (
# MAGIC   SELECT
# MAGIC     *,
# MAGIC     ROW_NUMBER() OVER (PARTITION BY message_id ORDER BY ingest_time, source_file) AS copy_number
# MAGIC   FROM workspace.hl7_silver.adt_events
# MAGIC   WHERE mrn IS NOT NULL AND visit_id IS NOT NULL AND admit_time IS NOT NULL
# MAGIC )
# MAGIC SELECT
# MAGIC   message_id, message_time, encounter_status,
# MAGIC   mrn, first_name, last_name, birth_date,
# MAGIC   COALESCE(gender, 'unknown')       AS gender,
# MAGIC   zip_code, visit_id, encounter_class, priority, location,
# MAGIC   COALESCE(attending, 'Unassigned') AS attending,
# MAGIC   admit_time, discharge_time, source_file, ingest_time
# MAGIC FROM numbered
# MAGIC WHERE copy_number = 1;

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE workspace.hl7_silver.patients AS
# MAGIC SELECT mrn, first_name, last_name, birth_date, gender, zip_code
# MAGIC FROM (
# MAGIC   SELECT
# MAGIC     *,
# MAGIC     ROW_NUMBER() OVER (PARTITION BY mrn ORDER BY message_time DESC) AS rn
# MAGIC   FROM workspace.hl7_silver.adt_events_clean
# MAGIC )
# MAGIC WHERE rn = 1;

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE TABLE IF NOT EXISTS workspace.hl7_silver.encounters (
# MAGIC   visit_id          STRING,
# MAGIC   mrn               STRING,
# MAGIC   location          STRING,
# MAGIC   attending         STRING,
# MAGIC   priority          STRING,
# MAGIC   admit_time        TIMESTAMP,
# MAGIC   discharge_time    TIMESTAMP,
# MAGIC   status            STRING,
# MAGIC   last_message_time TIMESTAMP
# MAGIC );
# MAGIC
# MAGIC MERGE INTO workspace.hl7_silver.encounters AS target
# MAGIC USING (
# MAGIC   SELECT
# MAGIC     visit_id,
# MAGIC     MAX_BY(mrn, message_time)       AS mrn,
# MAGIC     MAX_BY(location, message_time)  AS location,
# MAGIC     MAX_BY(attending, message_time) AS attending,
# MAGIC     MAX_BY(priority, message_time)  AS priority,
# MAGIC     MIN(admit_time)                 AS admit_time,
# MAGIC     MAX(discharge_time)             AS discharge_time,
# MAGIC     CASE WHEN MAX(discharge_time) IS NOT NULL THEN 'finished' ELSE 'in-progress' END AS status,
# MAGIC     MAX(message_time)               AS last_message_time
# MAGIC   FROM workspace.hl7_silver.adt_events_clean
# MAGIC   GROUP BY visit_id
# MAGIC ) AS source
# MAGIC ON target.visit_id = source.visit_id
# MAGIC WHEN MATCHED THEN UPDATE SET *
# MAGIC WHEN NOT MATCHED THEN INSERT *;

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT 'bronze rows' AS check_name, COUNT(*) AS n FROM workspace.hl7_bronze.fhir_bundles_raw
# MAGIC UNION ALL SELECT 'silver events',      COUNT(*) FROM workspace.hl7_silver.adt_events
# MAGIC UNION ALL SELECT 'rejects',            COUNT(*) FROM workspace.hl7_silver.adt_rejects
# MAGIC UNION ALL SELECT 'clean events',       COUNT(*) FROM workspace.hl7_silver.adt_events_clean
# MAGIC UNION ALL SELECT 'duplicates removed',
# MAGIC        (SELECT COUNT(*) FROM workspace.hl7_silver.adt_events)
# MAGIC      - (SELECT COUNT(*) FROM workspace.hl7_silver.adt_events_clean)
# MAGIC UNION ALL SELECT 'patients',           COUNT(*) FROM workspace.hl7_silver.patients
# MAGIC UNION ALL SELECT 'stays',              COUNT(*) FROM workspace.hl7_silver.encounters
# MAGIC UNION ALL SELECT 'finished stays',     COUNT(*) FROM workspace.hl7_silver.encounters WHERE status = 'finished'
# MAGIC UNION ALL SELECT 'still in hospital',  COUNT(*) FROM workspace.hl7_silver.encounters WHERE status = 'in-progress'
# MAGIC UNION ALL SELECT 'unassigned doctor',  COUNT(*) FROM workspace.hl7_silver.encounters WHERE attending = 'Unassigned';