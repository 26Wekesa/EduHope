from django.db import models

# Create your models here.
class Message(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField()
    subject = models.CharField(max_length=200)
    message = models.TextField()

    def __str__(self):
        return f"Message from {self.name} - {self.subject}"
    

class Mentor(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField()
    phone = models.IntegerField(max_length=10)
    message = models.TextField()
    organization = models.CharField(max_length=200)
    years = models.IntegerField()
    message = models.TextField()
    subject = models.CharField(max_length=200)

    def __str__(self):
        return f"Mentor from {self.name} - {self.email}"