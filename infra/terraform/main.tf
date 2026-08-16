/*
  Infrastructure Azure de la plateforme.

  Trois plans distincts :
  - operationnel : Container Apps (API) + PostgreSQL flexible (OLTP) ;
  - analytique   : ADLS Gen2 (lakehouse medallion) + Azure SQL (gold servi a
                   Power BI) + Data Factory (orchestration) ;
  - transverse   : Key Vault, Log Analytics, identites managees.

  Le compte de stockage active l'espace de noms hierarchique, condition
  necessaire pour qu'un raccourci OneLake Fabric puisse pointer dessus sans
  copie de donnees.
*/

terraform {
  required_version = ">= 1.6"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.100"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "azurerm" {
  features {
    key_vault {
      purge_soft_delete_on_destroy = false
    }
  }
}

data "azurerm_client_config" "current" {}

locals {
  prefix = "${var.project_name}-${var.environment}"

  tags = merge(var.tags, {
    projet      = var.project_name
    environment = var.environment
    gere_par    = "terraform"
  })
}

resource "random_password" "postgres" {
  length      = 32
  special     = true
  min_upper   = 2
  min_lower   = 2
  min_numeric = 2
  # Caracteres refuses par la chaine de connexion PostgreSQL.
  override_special = "!#%*-_=+"
}

resource "azurerm_resource_group" "main" {
  name     = "rg-${local.prefix}"
  location = var.location
  tags     = local.tags
}

# ---------------------------------------------------------------------------
# Observabilite et secrets
# ---------------------------------------------------------------------------

resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-${local.prefix}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_days
  tags                = local.tags
}

resource "azurerm_key_vault" "main" {
  name                       = replace("kv-${local.prefix}", "-", "")
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  purge_protection_enabled   = var.environment == "prod"
  soft_delete_retention_days = 7
  # L'acces se fait par RBAC : les strategies d'acces heritees sont figees et
  # difficiles a auditer.
  enable_rbac_authorization = true
  tags                      = local.tags
}

resource "azurerm_key_vault_secret" "postgres_password" {
  name         = "postgres-admin-password"
  value        = random_password.postgres.result
  key_vault_id = azurerm_key_vault.main.id

  depends_on = [azurerm_role_assignment.terraform_kv_admin]
}

resource "azurerm_role_assignment" "terraform_kv_admin" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

# ---------------------------------------------------------------------------
# Base operationnelle (OLTP)
# ---------------------------------------------------------------------------

resource "azurerm_postgresql_flexible_server" "oltp" {
  name                          = "psql-${local.prefix}"
  resource_group_name           = azurerm_resource_group.main.name
  location                      = azurerm_resource_group.main.location
  version                       = "16"
  administrator_login           = var.postgres_admin_login
  administrator_password        = random_password.postgres.result
  sku_name                      = var.postgres_sku
  storage_mb                    = var.postgres_storage_mb
  backup_retention_days         = var.environment == "prod" ? 35 : 7
  geo_redundant_backup_enabled  = var.environment == "prod"
  public_network_access_enabled = var.allow_public_access
  zone                          = "1"
  tags                          = local.tags

  lifecycle {
    # Un changement de mot de passe ne doit pas recreer le serveur.
    ignore_changes = [administrator_password, zone]
  }
}

resource "azurerm_postgresql_flexible_server_database" "collect" {
  name      = "collect"
  server_id = azurerm_postgresql_flexible_server.oltp.id
  charset   = "UTF8"
  collation = "fr_FR.utf8"
}

# ---------------------------------------------------------------------------
# Lakehouse (couches bronze, silver, gold)
# ---------------------------------------------------------------------------

resource "azurerm_storage_account" "lake" {
  name                     = replace("st${local.prefix}", "-", "")
  resource_group_name      = azurerm_resource_group.main.name
  location                 = azurerm_resource_group.main.location
  account_tier             = "Standard"
  account_replication_type = var.environment == "prod" ? "ZRS" : "LRS"
  account_kind             = "StorageV2"
  # Indispensable pour ADLS Gen2 et pour les raccourcis OneLake.
  is_hns_enabled                  = true
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  tags                            = local.tags

  blob_properties {
    versioning_enabled = true

    delete_retention_policy {
      days = 30
    }
  }
}

resource "azurerm_storage_data_lake_gen2_filesystem" "lakehouse" {
  name               = "lakehouse"
  storage_account_id = azurerm_storage_account.lake.id
}

# Une couche par repertoire racine : le cycle de vie et les droits different.
resource "azurerm_storage_data_lake_gen2_path" "layers" {
  for_each = toset(["bronze", "silver", "gold"])

  path               = each.value
  filesystem_name    = azurerm_storage_data_lake_gen2_filesystem.lakehouse.name
  storage_account_id = azurerm_storage_account.lake.id
  resource           = "directory"
}

# Le bronze est un journal : il est conserve longtemps mais descend
# rapidement en stockage froid pour limiter le cout.
resource "azurerm_storage_management_policy" "lifecycle" {
  storage_account_id = azurerm_storage_account.lake.id

  rule {
    name    = "bronze-archivage"
    enabled = true

    filters {
      prefix_match = ["lakehouse/bronze"]
      blob_types   = ["blockBlob"]
    }

    actions {
      base_blob {
        tier_to_cool_after_days_since_modification_greater_than    = 30
        tier_to_archive_after_days_since_modification_greater_than = 180
        delete_after_days_since_modification_greater_than          = var.bronze_retention_days
      }
    }
  }
}

# ---------------------------------------------------------------------------
# Entrepot servi a Power BI
# ---------------------------------------------------------------------------

resource "azurerm_mssql_server" "warehouse" {
  name                          = "sql-${local.prefix}"
  resource_group_name           = azurerm_resource_group.main.name
  location                      = azurerm_resource_group.main.location
  version                       = "12.0"
  minimum_tls_version           = "1.2"
  public_network_access_enabled = var.allow_public_access
  tags                          = local.tags

  azuread_administrator {
    login_username = var.sql_admin_group_name
    object_id      = var.sql_admin_group_object_id
    tenant_id      = data.azurerm_client_config.current.tenant_id
    # Authentification Entra ID exclusive : aucun mot de passe SQL a gerer.
    azuread_authentication_only = true
  }

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_mssql_database" "gold" {
  name           = "gold"
  server_id      = azurerm_mssql_server.warehouse.id
  sku_name       = var.sql_sku
  collation      = "French_CI_AS"
  max_size_gb    = var.sql_max_size_gb
  zone_redundant = var.environment == "prod"
  tags           = local.tags

  short_term_retention_policy {
    retention_days = var.environment == "prod" ? 35 : 7
  }
}

# ---------------------------------------------------------------------------
# Orchestration du pipeline
# ---------------------------------------------------------------------------

resource "azurerm_data_factory" "orchestration" {
  name                = "adf-${local.prefix}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = local.tags

  identity {
    type = "SystemAssigned"
  }
}

# Data Factory ecrit dans le lac : role de contributeur sur les donnees blob,
# et non Contributor, qui donnerait aussi les droits de gestion du compte.
resource "azurerm_role_assignment" "adf_lake" {
  scope                = azurerm_storage_account.lake.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_data_factory.orchestration.identity[0].principal_id
}

# ---------------------------------------------------------------------------
# API operationnelle
# ---------------------------------------------------------------------------

resource "azurerm_container_app_environment" "main" {
  name                       = "cae-${local.prefix}"
  resource_group_name        = azurerm_resource_group.main.name
  location                   = azurerm_resource_group.main.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  tags                       = local.tags
}

resource "azurerm_container_app" "api" {
  name                         = "ca-${local.prefix}-api"
  resource_group_name          = azurerm_resource_group.main.name
  container_app_environment_id = azurerm_container_app_environment.main.id
  revision_mode                = "Single"
  tags                         = local.tags

  identity {
    type = "SystemAssigned"
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    transport        = "auto"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = var.api_min_replicas
    max_replicas = var.api_max_replicas

    container {
      name   = "api"
      image  = var.api_image
      cpu    = 0.5
      memory = "1Gi"

      env {
        name  = "ENVIRONMENT"
        value = var.environment
      }
      env {
        name  = "LAKE_ROOT"
        value = "abfss://lakehouse@${azurerm_storage_account.lake.name}.dfs.core.windows.net"
      }
      env {
        name  = "WAREHOUSE_DIALECT"
        value = "tsql"
      }
      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }
      env {
        name        = "SECRET_KEY"
        secret_name = "app-secret-key"
      }

      liveness_probe {
        transport = "HTTP"
        port      = 8000
        path      = "/health"
      }
      readiness_probe {
        transport = "HTTP"
        port      = 8000
        path      = "/health"
      }
    }

    # Montee en charge sur le nombre de requetes concurrentes : la collecte
    # connait des pointes marquees en fin de journee, quand les tablettes se
    # synchronisent toutes au retour du terrain.
    http_scale_rule {
      name                = "requetes-concurrentes"
      concurrent_requests = 50
    }
  }

  secret {
    name  = "database-url"
    value = "postgresql+psycopg://${var.postgres_admin_login}:${random_password.postgres.result}@${azurerm_postgresql_flexible_server.oltp.fqdn}:5432/collect?sslmode=require"
  }

  secret {
    name  = "app-secret-key"
    value = random_password.postgres.result
  }
}

resource "azurerm_role_assignment" "api_lake" {
  scope                = azurerm_storage_account.lake.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_container_app.api.identity[0].principal_id
}

resource "azurerm_role_assignment" "api_keyvault" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_container_app.api.identity[0].principal_id
}
