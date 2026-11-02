import smtplib, argparse
from email.message import EmailMessage

def send_email(_from, to, subject, body, importance="Normal") -> bool:
    try:
        smtp_server = smtplib.SMTP('server_address', 25)
        # smtp_server.starttls()
        message = EmailMessage()
        message.set_content(body)
        message['Subject'] = subject
        message['From'] = _from
        message['To'] = ', '.join(to)
        if importance == "High":
            message['Importance'] = 'High'

        smtp_server.send_message(message)
        # smtp_server.sendmail(_from, to, message.as_string())
        smtp_server.quit()
        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        return False
    
def load_txt(fp):
    with open(fp, 'r') as f:
        to_list = [line.strip() for line in f if line.strip()]
        return to_list

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Update SNMP configuration file.")
    parser.add_argument("--from", dest="from_addr", help="From address for the email", required=True)
    parser.add_argument("--to-file", help="Path to file containing recipient emails (one per line)", required=True)
    parser.add_argument("--subject", help="Subject for the email", required=True)
    parser.add_argument("--body", help="Body for the email", required=True)
    parser.add_argument("--importance", help="Importance of the email (High, Normal, Low)", choices=["High", "Normal", "Low"], default="Normal")

    args = parser.parse_args()
    
    # Read emails from file
    to_list = load_txt(args.to_file)
    send_email(args.from_addr, to_list, args.subject, args.body)
    