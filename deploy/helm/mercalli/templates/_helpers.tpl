{{- define "mercalli.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "mercalli.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "mercalli.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "mercalli.selectorLabels" -}}
app.kubernetes.io/name: {{ include "mercalli.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "mercalli.labels" -}}
helm.sh/chart: {{ include "mercalli.chart" . }}
{{ include "mercalli.selectorLabels" . }}
app.kubernetes.io/version: {{ .Values.image.tag | default .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "mercalli.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "mercalli.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/* The Secret holding the database URL: the user's own, or the one this chart creates. */}}
{{- define "mercalli.databaseSecret" -}}
{{- if .Values.database.existingSecret }}
{{- .Values.database.existingSecret }}
{{- else if .Values.database.url }}
{{- printf "%s-database" (include "mercalli.fullname" .) }}
{{- else }}
{{- fail "Set database.existingSecret (preferred) or database.url: Mercalli on Kubernetes needs PostgreSQL." }}
{{- end }}
{{- end }}
