from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import redirect, render


def home(request):
    return render(request, "frontend/home.html")


def login_view(request):
    if request.method != "POST":
        return redirect("frontend-home")

    username = request.POST.get("username", "").strip()
    password = request.POST.get("password", "")

    if not username or not password:
        messages.error(request, "Введите логин и пароль.")
        return redirect("frontend-home")

    user = authenticate(request, username=username, password=password)
    if user is None:
        messages.error(request, "Неверный логин или пароль.")
        return redirect("frontend-home")

    login(request, user)
    messages.success(request, "Вы вошли в систему.")
    return redirect("frontend-home")


def logout_view(request):
    logout(request)
    messages.info(request, "Вы вышли из системы.")
    return redirect("frontend-home")
