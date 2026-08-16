output "api_url" {
  description = "URL publique de l'API operationnelle."
  value       = "https://${azurerm_container_app.api.ingress[0].fqdn}"
}

output "lake_abfss_root" {
  description = "Racine ABFSS du lakehouse, a renseigner dans LAKE_ROOT."
  value       = "abfss://${azurerm_storage_data_lake_gen2_filesystem.lakehouse.name}@${azurerm_storage_account.lake.name}.dfs.core.windows.net"
}

output "gold_layer_path" {
  description = "Chemin de la couche gold, source du modele semantique Power BI."
  value       = "abfss://${azurerm_storage_data_lake_gen2_filesystem.lakehouse.name}@${azurerm_storage_account.lake.name}.dfs.core.windows.net/gold"
}

output "warehouse_server_fqdn" {
  description = "Serveur Azure SQL hebergeant la couche gold."
  value       = azurerm_mssql_server.warehouse.fully_qualified_domain_name
}

output "postgres_fqdn" {
  description = "Serveur PostgreSQL de la base operationnelle."
  value       = azurerm_postgresql_flexible_server.oltp.fqdn
}

output "key_vault_uri" {
  description = "URI du coffre de secrets."
  value       = azurerm_key_vault.main.vault_uri
}

output "data_factory_name" {
  description = "Fabrique de donnees orchestrant le pipeline medallion."
  value       = azurerm_data_factory.orchestration.name
}

output "resource_group" {
  description = "Groupe de ressources cree."
  value       = azurerm_resource_group.main.name
}
