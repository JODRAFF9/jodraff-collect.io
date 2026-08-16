variable "project_name" {
  description = "Prefixe de nommage des ressources."
  type        = string
  default     = "jodraff"

  validation {
    # Les noms de compte de stockage n'acceptent que 24 caracteres alphanumeriques.
    condition     = can(regex("^[a-z0-9]{3,12}$", var.project_name))
    error_message = "project_name doit comporter de 3 a 12 caracteres alphanumeriques minuscules."
  }
}

variable "environment" {
  description = "Environnement cible."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "recette", "prod"], var.environment)
    error_message = "environment doit valoir dev, recette ou prod."
  }
}

variable "location" {
  description = "Region Azure."
  type        = string
  default     = "francecentral"
}

variable "tags" {
  description = "Etiquettes appliquees a toutes les ressources."
  type        = map(string)
  default     = {}
}

# --- Base operationnelle ---------------------------------------------------

variable "postgres_admin_login" {
  description = "Identifiant administrateur PostgreSQL."
  type        = string
  default     = "jodraffadmin"
}

variable "postgres_sku" {
  description = "Reference de calcul du serveur PostgreSQL."
  type        = string
  default     = "B_Standard_B2s"
}

variable "postgres_storage_mb" {
  description = "Stockage du serveur PostgreSQL, en mebioctets."
  type        = number
  default     = 32768
}

# --- Entrepot --------------------------------------------------------------

variable "sql_sku" {
  description = "Reference de la base Azure SQL servant la couche gold."
  type        = string
  default     = "S1"
}

variable "sql_max_size_gb" {
  description = "Taille maximale de la base gold, en gibioctets."
  type        = number
  default     = 50
}

variable "sql_admin_group_name" {
  description = "Nom du groupe Entra ID administrateur de l'instance SQL."
  type        = string
}

variable "sql_admin_group_object_id" {
  description = "Identifiant d'objet du groupe Entra ID administrateur."
  type        = string
}

# --- Lakehouse -------------------------------------------------------------

variable "bronze_retention_days" {
  description = <<-EOT
    Duree de conservation des fichiers bronze. La couche bronze est le journal
    d'origine de la collecte : elle doit couvrir au moins la duree d'archivage
    legale des donnees d'enquete, puisqu'elle seule permet de rejouer
    integralement les couches superieures.
  EOT
  type        = number
  default     = 2555 # sept ans
}

# --- API -------------------------------------------------------------------

variable "api_image" {
  description = "Image conteneur de l'API operationnelle."
  type        = string
  default     = "ghcr.io/jodraff9/jodraff-collect-api:latest"
}

variable "api_min_replicas" {
  description = "Nombre minimal de replicas de l'API."
  type        = number
  default     = 1
}

variable "api_max_replicas" {
  description = "Nombre maximal de replicas de l'API."
  type        = number
  default     = 10
}

# --- Reseau ----------------------------------------------------------------

variable "allow_public_access" {
  description = <<-EOT
    Autorise l'acces public aux bases. A laisser a true en developpement
    seulement : en production, basculer sur des points de terminaison prives
    et un reseau virtuel dedie.
  EOT
  type        = bool
  default     = true
}

variable "log_retention_days" {
  description = "Retention des journaux dans Log Analytics."
  type        = number
  default     = 30
}
