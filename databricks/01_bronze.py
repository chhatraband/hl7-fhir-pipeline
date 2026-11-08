# Databricks notebook source
from pyspark.sql.functions import col, current_timestamp

LANDING = "/Volumes/workspace/hl7_bronze/landing/"           # where the files are
BRONZE_TABLE = "workspace.hl7_bronze.fhir_bundles_raw"       # the table we create

# COMMAND ----------

raw = (
    spark.read
        .text(LANDING, wholetext=True)               # whole file = one row (set it HERE)
        .select(
            col("value"),                            # the full JSON text
            col("_metadata.file_path").alias("source_file"),   # which file it came from
        )
        .withColumn("ingest_time", current_timestamp())        # when we loaded it
)

print("Files read:", raw.count())
display(raw.limit(5))

# COMMAND ----------

if spark.catalog.tableExists(BRONZE_TABLE):
    already_loaded = spark.table(BRONZE_TABLE).select("source_file")
    new_rows = raw.join(already_loaded, on="source_file", how="left_anti")
else:
    new_rows = raw                                   # first run: everything is new

print("New files to load:", new_rows.count())

# COMMAND ----------

new_rows.write.mode("append").saveAsTable(BRONZE_TABLE)

print("Bronze rows now:", spark.table(BRONZE_TABLE).count())

# COMMAND ----------

# MAGIC %sql
# MAGIC DESCRIBE HISTORY workspace.hl7_bronze.fhir_bundles_raw

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT COUNT(*) AS rows_at_version_0
# MAGIC FROM workspace.hl7_bronze.fhir_bundles_raw VERSION AS OF 0

# COMMAND ----------

