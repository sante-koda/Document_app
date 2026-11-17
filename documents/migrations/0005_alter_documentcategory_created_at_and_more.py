from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('documents', '0004_document_current_version'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='DocumentCategory',
            name='created_at',
        ),
        migrations.RemoveField(
            model_name='DocumentCategory',
            name='updated_at',
        ),
        migrations.AddField(
            model_name='DocumentCategory',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True),
        ),
        migrations.AddField(
            model_name='DocumentCategory',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
    ]