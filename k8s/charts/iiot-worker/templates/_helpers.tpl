{{- define "iiot-worker.labels" -}}
app.kubernetes.io/name: iiot-worker
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: iiot-secure-monitor
{{- end }}

{{- define "iiot-worker.selector" -}}
app.kubernetes.io/name: iiot-worker
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "iiot-worker.image" -}}
{{- if not (regexMatch "^[0-9a-f]{40}$" (.Values.image.tag | toString)) -}}
{{- fail "image.tag must be the 40-character commit SHA of a CI-built image (never latest)" -}}
{{- end -}}
{{- required "image.repository is required" .Values.image.repository }}:{{ .Values.image.tag }}
{{- end }}
