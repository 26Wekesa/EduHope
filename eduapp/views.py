from django.shortcuts import render
from eduapp.models import Message, Mentor
from django.shortcuts import redirect
from django.contrib import messages

# Create your views here.

def index(request):
    return render(request, 'index.html')

def services(request):
    return render(request, 'service-details.html')

def terms(request):
    return render(request, 'terms.html')

def privacy(request):
    return render(request, 'privacy.html')

def sponser(request):
    return render(request, 'sponser.html')

#mentor section
def mentor(request):
    # Mentor application form (front-end focused). Submissions are saved as Message entries.
    if request.method == "POST":
        mymentor = Mentor(
            name = request.POST['name'],
            email = request.POST['email'],
            phone = request.POST['phone'],
            years=request.POST['years'],
            organization = request.POST['organization'],
            message = request.POST['message'],
            subject = request.POST['subject'],
        )

        mymentor.save()
        messages.success(request, "Thank you for applying as a Mentor")
        return redirect('/mentor')
    else:
        return render(request, 'mentor.html')
    


def admin(request):
   all = Message.objects.all()
   return render(request, 'admin_view.html', {"all": all})


def patner(request):
    return render(request, 'patner.html')


def events(request):
    return render(request, 'events.html')


def learner(request):
    return render(request, 'learner.html')

def message(request):
    if request.method == "POST":
        mymessage = Message(
            name = request.POST['name'],
            email = request.POST['email'],
            subject = request.POST['subject'],
            message = request.POST['message'],
        )

        mymessage.save()
        messages.success(request, "Thank you for contacting Us")
        return redirect('/message')
    else:
        return render(request, 'message.html')


# def appointment(request):
#     """Simple confirmation page shown after a message is saved."""
#     return render(request, 'appointment.html')

