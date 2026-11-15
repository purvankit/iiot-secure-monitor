output "endpoint" {
  description = "Device data endpoint (ATS) for MQTT over TLS on 8883"
  value       = data.aws_iot_endpoint.ats.endpoint_address
}

output "certificate_pems" {
  description = "Signed device certificates (public, but the provider marks them sensitive)"
  sensitive   = true
  value       = { for k, c in aws_iot_certificate.device : k => c.certificate_pem }
}

output "thing_names" {
  value = [for t in aws_iot_thing.device : t.name]
}
