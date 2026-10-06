using Microsoft.SemanticKernel;
using System.ComponentModel;

public class CalendarPlugin
{
    [KernelFunction("book_meeting")]
    [Description("Book a meeting on the user's calendar")]
    public string BookMeeting(string title, string time) => "booked";
}
