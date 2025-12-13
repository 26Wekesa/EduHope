from django.shortcuts import render
from eduapp.models import *
from django.shortcuts import redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login

# Create your views here.

def index(request):
    return render(request, 'index.html')

def login_user(request):
    if request.method == "POST":
        username = request.POST['username']
        password = request.POST['password']

        user = authenticate(request, username=username, password=password)

        # Check if the user exists
        if user is not None:
            # login(request, user)
            login(request,user)
            messages.success(request, "You are now logged in!")
            # Admin
            if user.is_superuser:
                return redirect('/admin_view')

            # For Normal Users
            return redirect('/index')
        else:
            messages.error(request, "Invalid login credentials")

    return render(request, 'login.html')
    

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
    # Query sets for listings
    all_messages = Message.objects.all()
    mentors = Mentor.objects.all()
    applications = SupportApplication.objects.all()
    try:
        partnerships = PartnershipRequest.objects.all()
    except:
        partnerships = []
    try:
        bookings = MentorshipBooking.objects.all()
    except:
        bookings = []
    try:
        donations = Donation.objects.all()
    except:
        donations = []

    # Totals for the stats cards (provide safe defaults if models are missing)
    total_messages = Message.objects.count()
    total_mentors = Mentor.objects.count()
    total_learners = SupportApplication.objects.count()
    try:
        total_partners = PartnershipRequest.objects.count()
        approved_partners = PartnershipRequest.objects.filter(approved=True).count()
    except:
        total_partners = 0
        approved_partners = 0
    try:
        total_bookings = MentorshipBooking.objects.count()
    except:
        total_bookings = 0
    try:
        total_donations = Donation.objects.count()
    except:
        total_donations = 0
    try:
        approved_learners = SupportApplication.objects.filter(approved=True).count()
        approved_mentors = Mentor.objects.filter(approved=True).count()
    except:
        approved_learners = 0
        approved_mentors = 0

    # Partners model may not be defined in this repo; provide safe empty values
    verified_partners = 0
    all_partners = partnerships

    return render(request, 'admin_view.html', {
         "all_messages": all_messages,
         "mentors": mentors,
         "applications": applications,
         "all_partners": all_partners,
         "bookings": bookings,
         "donations": donations,
         "total_messages": total_messages,
         "total_mentors": total_mentors,
         "total_learners": total_learners,
         "total_partners": total_partners,
         "total_bookings": total_bookings,
         "total_donations": total_donations,
         "approved_learners": approved_learners,
         "approved_mentors": approved_mentors,
         "approved_partners": approved_partners,
         "verified_partners": approved_partners,
    })
   


def patner(request):
    if request.method == "POST":
        if 'partnership' in request.POST:
            try:
                mypartnership = PartnershipRequest(
                    org_name=request.POST['org_name'],
                    partner_type=request.POST['partner_type'],
                    email=request.POST['email'],
                    phone=request.POST['phone'],
                    website=request.POST.get('website', ''),
                    partnership_area=request.POST['partnership_area'],
                    proposal_description=request.POST['proposal_description'],
                    reason=request.POST['reason'],
                )
                mypartnership.save()
                messages.success(request, "Thank you for your partnership request. We will review it and get back to you.")
            except:
                messages.error(request, "Error saving partnership request.")
            return redirect('/patner')
        elif 'donation' in request.POST:
            try:
                mydonation = Donation(
                    donor_name=request.POST['donor_name'],
                    email=request.POST['email'],
                    phone=request.POST.get('phone', ''),
                    amount=request.POST['amount'],
                    donation_type=request.POST['donation_type'],
                    message=request.POST.get('message', ''),
                )
                mydonation.save()
                messages.success(request, "Thank you for your donation!")
            except:
                messages.error(request, "Error processing donation.")
            return redirect('/patner')
    return render(request, 'patner.html')


def mentorship_booking(request):
    if request.method == "POST":
        try:
            mybooking = MentorshipBooking(
                school_name=request.POST['school_name'],
                contact_person=request.POST['contact_person'],
                email=request.POST['email'],
                phone=request.POST['phone'],
                number_of_learners=request.POST['number_of_learners'],
                preferred_date=request.POST['preferred_date'],
                preferred_time=request.POST['preferred_time'],
                duration_hours=request.POST['duration_hours'],
                topics=request.POST['topics'],
                additional_notes=request.POST.get('additional_notes', ''),
            )
            mybooking.save()
            messages.success(request, "Thank you for booking a mentorship session. We will confirm the details soon.")
        except:
            messages.error(request, "Error booking mentorship session.")
        return redirect('/mentorship_booking')
    return render(request, 'mentorship_booking.html')


def events(request):
    return render(request, 'events.html')

def register(request):
    """ Show the registration form """
    if request.method == 'POST':
        username = request.POST['username']
        password = request.POST['password']
        confirm_password = request.POST['confirm_password']

        # Check the password
        if password == confirm_password:
            try:
                user = User.objects.create_user(username=username, password=password)
                user.save()

                # Display a message
                messages.success(request, "Account created successfully")
                return redirect('login/')
            except:
                # Display a message if the above fails
                messages.error(request, "Username already exist")
        else:
            # Display a message saying passwords don't match
            messages.error(request, "Passwords do not match")

    return render(request, 'register.html')


def learner(request):
    if request.method == "POST":
        try:
            mysupport_applications = SupportApplication(
                learner_name=request.POST["learner_name"],
                dob=request.POST["dob"],
                grade=request.POST["grade"],
                school=request.POST["school"],

                support_type=request.POST["support_type"],
                situation_description=request.POST["situation_description"],

                guardian_name=request.POST["guardian_name"],
                relationship=request.POST["relationship"],
                guardian_contact=request.POST["guardian_contact"],
                address=request.POST["address"],

                informant_name=request.POST["informant_name"],
                informant_phone=request.POST["informant_phone"],
                informant_relationship=request.POST["informant_relationship"],
                informant_remarks=request.POST.get("informant_remarks", ""),
            )
            mysupport_applications.save()
            messages.success(request, "Thank you for sending your application for assistance. We shall reach out to you")
            return redirect("/learner")
        except Exception as e:
            messages.error(request, f"Error saving application: {str(e)}")
            print(f"Error: {str(e)}")
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
    

def support_application(request):
    if request.method == "POST":
        mysupport_applications = SupportApplication(
            learner_name=request.POST["learner_name"],
            dob=request.POST["dob"],
            grade=request.POST["grade"],
            school=request.POST["school"],

            support_type=request.POST["support_type"],
            situation_description=request.POST["situation_description"],

            guardian_name=request.POST["guardian_name"],
            relationship=request.POST["relationship"],
            guardian_contact=request.POST["guardian_contact"],
            address=request.POST["address"],

            informant_name=request.POST["informant_name"],
            informant_phone=request.POST["informant_phone"],
            informant_relationship=request.POST["informant_relationship"],
            informant_remarks=request.POST["informant_remarks"],
        )
        mysupport_applications.save()
        messages.success(request, "Thank you for sending your application for assistance. We shall reach out to you")
        return redirect("/learner")
    else:
        return render(request, "learner.html")
           # Create this page or change it

def delete(request, id):
    mymessage = Message.objects.get(id=id)
    mymessage.delete()
    messages.success(request, "Message deleted successfully.")
    return redirect('/admin_view')


def approve_mentor(request, id):
    try:
        mentor = Mentor.objects.get(id=id)
        mentor.approved = True
        mentor.save()
        messages.success(request, "Mentor approved successfully.")
    except:
        messages.error(request, "Error approving mentor.")
    return redirect('/admin_view')


def delete_mentor(request, id):
    try:
        mentor = Mentor.objects.get(id=id)
        mentor.delete()
        messages.success(request, "Mentor deleted successfully.")
    except:
        messages.error(request, "Error deleting mentor.")
    return redirect('/admin_view')


def approve_learner(request, id):
    try:
        learner = SupportApplication.objects.get(id=id)
        learner.approved = True
        learner.save()
        messages.success(request, "Learner approved successfully.")
    except:
        messages.error(request, "Error approving learner.")
    return redirect('/admin_view')


def delete_learner(request, id):
    try:
        learner = SupportApplication.objects.get(id=id)
        learner.delete()
        messages.success(request, "Learner application deleted successfully.")
    except:
        messages.error(request, "Error deleting learner application.")
    return redirect('/admin_view')


def approve_partner(request, id):
    try:
        partner = PartnershipRequest.objects.get(id=id)
        partner.approved = True
        partner.save()
        messages.success(request, "Partner approved successfully.")
    except:
        messages.error(request, "Error approving partner.")
    return redirect('/admin_view')


def delete_partner(request, id):
    try:
        partner = PartnershipRequest.objects.get(id=id)
        partner.delete()
        messages.success(request, "Partner request deleted successfully.")
    except:
        messages.error(request, "Error deleting partner request.")
    return redirect('/admin_view')


def count(request):
     total_messages = Message.objects.count()
     total_mentors = Mentor.objects.count()
     total_applications = SupportApplication.objects.count()
     return render(request, 'admin_view.html', {
         "total_messages": total_messages,
         "total_mentors": total_mentors,
         "total_applications": total_applications
     })
   


# def appointment(request):
#     """Simple confirmation page shown after a message is saved."""
#     return render(request, 'appointment.html')

