import django.db.models.deletion
import pgvector.django.vector
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('agent', '0001_vector_extension'),
    ]

    operations = [
        migrations.CreateModel(
            name='Repository',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('url', models.TextField(unique=True)),
                ('name', models.CharField(max_length=255)),
                ('last_analyzed_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name_plural': 'repositories',
                'ordering': ('-last_analyzed_at', '-created_at'),
            },
        ),
        migrations.CreateModel(
            name='ResearchSession',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('question', models.TextField()),
                ('question_embedding', pgvector.django.vector.VectorField(blank=True, dimensions=384, null=True)),
                ('answer', models.TextField(blank=True, null=True)),
                ('source', models.CharField(blank=True, choices=[('cache', 'cache'), ('llm_knowledge', 'llm_knowledge'), ('readme_scan', 'readme_scan'), ('full_traversal', 'full_traversal')], max_length=32, null=True)),
                ('token_usage', models.JSONField(blank=True, null=True)),
                ('started_at', models.DateTimeField(auto_now_add=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('repository', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sessions', to='agent.repository')),
            ],
            options={
                'ordering': ('-started_at',),
            },
        ),
        migrations.CreateModel(
            name='Finding',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('file_path', models.TextField()),
                ('line_start', models.IntegerField(blank=True, null=True)),
                ('line_end', models.IntegerField(blank=True, null=True)),
                ('note', models.TextField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='findings', to='agent.researchsession')),
            ],
            options={
                'ordering': ('created_at',),
                'indexes': [models.Index(fields=['file_path'], name='agent_findi_file_pa_f69464_idx')],
            },
        ),
        migrations.CreateModel(
            name='ToolCallLog',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('tool_name', models.CharField(max_length=64)),
                ('input_params', models.JSONField()),
                ('output_summary', models.TextField()),
                ('called_at', models.DateTimeField(auto_now_add=True)),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tool_calls', to='agent.researchsession')),
            ],
            options={
                'ordering': ('called_at',),
                'indexes': [models.Index(fields=['tool_name'], name='agent_toolc_tool_na_7db8bc_idx')],
            },
        ),
    ]
