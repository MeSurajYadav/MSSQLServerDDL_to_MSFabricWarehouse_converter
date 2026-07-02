# MSSQLServerDDL_to_MSFabricWarehouse_converter
This python all can be used to convert sql server ddl to ms fabric ddl. This is useful in migration projects.

# How to use
> python convert_sqlserver_to_fabric.py sql_server_compatible.txt ms_fabric_warehouse_compatible.txt
### This will generate the output file along with the outputfilename.report.txt file with all the analysis report and tell you what are all the conversions this code done for you.
### for example 2 something like below:

SQL Server to Microsoft Fabric Warehouse conversion report

Counts:
- binary_to_varbinary: 2190
- clustered_primary_keys_converted_to_nonclustered: 300
- create_schema: 5
- create_table: 1500
- create_table_storage_or_index_options_removed: 1500
- datetime2_7_to_datetime2_6: 2002
- datetime_to_datetime2_6: 40
- datetimeoffset_to_datetime2_6: 1
- default_constraints_removed: 1200
- identifiers_renamed: 1
- image_to_varbinary_max: 1
- money_to_decimal_19_4: 7
- ntext_to_varchar_max: 1
- nvarchar_max_to_varchar_max: 40
- nvarchar_to_varchar_length_doubled: 1147
- primary_keys_moved_to_alter_table: 1228
- set_statements_removed: 2000
- sparse_keywords_removed: 2
- tinyint_to_smallint: 24
- unnamed_primary_keys_named: 11
- use_statements_removed: 1

Notes:
- Renamed identifier [SGY\Admins] to [SGY_Admins] because Fabric schema/table names cannot contain / or \ or end with dot.
