from django.contrib import admin

from .models import Package, Prediction, RecentWin, Subscription, Testimonial

admin.site.register([Package, Prediction, RecentWin, Subscription, Testimonial])

