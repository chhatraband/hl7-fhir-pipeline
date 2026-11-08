# Databricks notebook source
# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE workspace.hl7_gold.dim_patient AS
# MAGIC SELECT
# MAGIC   patient_key,
# MAGIC   gender,
# MAGIC   birth_date,
# MAGIC   age,
# MAGIC   CASE
# MAGIC     WHEN age < 35 THEN '18-34'
# MAGIC     WHEN age < 50 THEN '35-49'
# MAGIC     WHEN age < 65 THEN '50-64'
# MAGIC     ELSE '65+'
# MAGIC   END AS age_group,
# MAGIC   zip_code
# MAGIC FROM (
# MAGIC   SELECT
# MAGIC     mrn AS patient_key,
# MAGIC     gender,
# MAGIC     birth_date,
# MAGIC     zip_code,
# MAGIC     CAST(FLOOR(MONTHS_BETWEEN(current_date(), birth_date) / 12) AS INT) AS age
# MAGIC   FROM workspace.hl7_silver.patients
# MAGIC );

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE workspace.hl7_gold.dim_location AS
# MAGIC SELECT DISTINCT
# MAGIC   location AS location_key,
# MAGIC   regexp_extract(location, '^(.*)-([^-]+)-([^-]+)$', 1) AS department,
# MAGIC   regexp_extract(location, '^(.*)-([^-]+)-([^-]+)$', 2) AS room,
# MAGIC   regexp_extract(location, '^(.*)-([^-]+)-([^-]+)$', 3) AS bed
# MAGIC FROM workspace.hl7_silver.encounters;
# MAGIC
# MAGIC

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE workspace.hl7_gold.dim_date AS
# MAGIC WITH bounds AS (
# MAGIC   SELECT to_date(MIN(admit_time)) AS start_date
# MAGIC   FROM workspace.hl7_silver.encounters
# MAGIC ),
# MAGIC days AS (
# MAGIC   SELECT explode(sequence(start_date, current_date(), INTERVAL 1 DAY)) AS d
# MAGIC   FROM bounds
# MAGIC )
# MAGIC SELECT
# MAGIC   CAST(date_format(d, 'yyyyMMdd') AS INT) AS date_key,
# MAGIC   d                          AS date,
# MAGIC   date_format(d, 'EEEE')     AS day_name,
# MAGIC   month(d)                   AS month,
# MAGIC   date_format(d, 'MMMM')     AS month_name,
# MAGIC   quarter(d)                 AS quarter,
# MAGIC   year(d)                    AS year,
# MAGIC   dayofweek(d) IN (1, 7)     AS is_weekend
# MAGIC FROM days;

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE workspace.hl7_gold.fact_encounters AS
# MAGIC WITH ordered AS (
# MAGIC   SELECT
# MAGIC     e.*,
# MAGIC     LAG(discharge_time) OVER (PARTITION BY mrn ORDER BY admit_time) AS prev_discharge_time
# MAGIC   FROM workspace.hl7_silver.encounters e
# MAGIC )
# MAGIC SELECT
# MAGIC   visit_id,
# MAGIC   mrn                                                    AS patient_key,
# MAGIC   location                                               AS location_key,
# MAGIC   CAST(date_format(admit_time, 'yyyyMMdd') AS INT)       AS admit_date_key,
# MAGIC   admit_time,
# MAGIC   discharge_time,
# MAGIC   ROUND((unix_timestamp(discharge_time) - unix_timestamp(admit_time)) / 3600, 1) AS los_hours,
# MAGIC   status,
# MAGIC   priority,
# MAGIC   attending,
# MAGIC   prev_discharge_time IS NOT NULL
# MAGIC     AND admit_time <= prev_discharge_time + INTERVAL 30 DAYS AS is_readmission_30d
# MAGIC FROM ordered;
# MAGIC

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT 'fact rows' AS check_name, CAST(COUNT(*) AS DOUBLE) AS value
# MAGIC   FROM workspace.hl7_gold.fact_encounters
# MAGIC UNION ALL SELECT 'readmissions', COUNT_IF(is_readmission_30d)
# MAGIC   FROM workspace.hl7_gold.fact_encounters
# MAGIC UNION ALL SELECT 'readmission rate %', ROUND(100 * AVG(CAST(is_readmission_30d AS INT)), 1)
# MAGIC   FROM workspace.hl7_gold.fact_encounters
# MAGIC UNION ALL SELECT 'avg LOS hours (finished)', ROUND(AVG(los_hours), 1)
# MAGIC   FROM workspace.hl7_gold.fact_encounters WHERE los_hours IS NOT NULL
# MAGIC UNION ALL SELECT 'still in hospital (no LOS)', COUNT_IF(los_hours IS NULL)
# MAGIC   FROM workspace.hl7_gold.fact_encounters
# MAGIC UNION ALL SELECT 'departments', COUNT(DISTINCT department)
# MAGIC   FROM workspace.hl7_gold.dim_location
# MAGIC UNION ALL SELECT 'orphan patients', COUNT(*)
# MAGIC   FROM workspace.hl7_gold.fact_encounters f
# MAGIC   LEFT JOIN workspace.hl7_gold.dim_patient p ON f.patient_key = p.patient_key
# MAGIC   WHERE p.patient_key IS NULL
# MAGIC UNION ALL SELECT 'orphan locations', COUNT(*)
# MAGIC   FROM workspace.hl7_gold.fact_encounters f
# MAGIC   LEFT JOIN workspace.hl7_gold.dim_location l ON f.location_key = l.location_key
# MAGIC   WHERE l.location_key IS NULL
# MAGIC UNION ALL SELECT 'orphan dates', COUNT(*)
# MAGIC   FROM workspace.hl7_gold.fact_encounters f
# MAGIC   LEFT JOIN workspace.hl7_gold.dim_date d ON f.admit_date_key = d.date_key
# MAGIC   WHERE d.date_key IS NULL;

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT patient_key, visit_id, admit_time, discharge_time, los_hours, is_readmission_30d
# MAGIC FROM workspace.hl7_gold.fact_encounters
# MAGIC WHERE patient_key IN (
# MAGIC   SELECT patient_key FROM workspace.hl7_gold.fact_encounters WHERE is_readmission_30d
# MAGIC )
# MAGIC ORDER BY patient_key, admit_time
# MAGIC LIMIT 10;

# COMMAND ----------

