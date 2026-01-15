from django.urls import path
from .views import home, login_view, logout_view

urlpatterns = [
    path('', home, name='frontend-home'),
    path('login/', login_view, name='frontend-login'),
    path('logout/', logout_view, name='frontend-logout'),
]
