# -*- coding: utf-8 -*-
# Generated by Django 1.9.1 on 2016-05-13 11:15
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contentstore', '0002_messageset_content_type'),
    ]

    operations = [
        migrations.AlterField(
            model_name='messageset',
            name='short_name',
            field=models.CharField(max_length=100, unique=True, verbose_name='Short name'),
        ),
    ]
