
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('eduapp.urls')),
   
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=str(settings.BASE_DIR / 'static'))
