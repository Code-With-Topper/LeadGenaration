from django.contrib import admin
from .models import Quotation, QuotationItem

admin.site.register(Quotation)
admin.site.register(QuotationItem)
