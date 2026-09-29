from django.db import models


class SavedReportFilter(models.Model):
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="saved_report_filters")
    name = models.CharField(max_length=100)
    report_key = models.CharField(max_length=60)
    filters = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "name", "report_key"], name="unique_saved_report_filter")]