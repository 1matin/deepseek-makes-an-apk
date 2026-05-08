from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.clock import Clock
from kivy.storage.jsonstore import JsonStore
from kivy.utils import platform
from plyer import sms as plyer_sms
import time
import threading

if platform == 'android':
    from jnius import autoclass, cast
    from android import mActivity
    from android.broadcast import BroadcastReceiver

    PythonService = autoclass('org.kivy.android.PythonService')
    Intent = autoclass('android.content.Intent')
    IntentFilter = autoclass('android.content.IntentFilter')
    Uri = autoclass('android.net.Uri')
    ContentValues = autoclass('android.content.ContentValues')
    Calendar = autoclass('java.util.Calendar')

class ChatApp(App):
    number_file = 'target_number.json'
    messages_store_file = 'messages.json'

    def build(self):
        self.store = JsonStore(self.number_file)
        self.msg_store = JsonStore(self.messages_store_file)

        # Load saved phone number
        self.target_number = self.store.get('number') if self.store.exists('number') else None

        root = BoxLayout(orientation='vertical', padding=10, spacing=10)

        # Phone number field (persistent)
        self.number_input = TextInput(
            hint_text='Enter phone number',
            text=self.target_number if self.target_number else '',
            multiline=False,
            size_hint=(1, None),
            height=50
        )
        root.add_widget(self.number_input)

        # Save button for number
        save_btn = Button(text='Save Number', size_hint=(1, None), height=40)
        save_btn.bind(on_press=self.save_number)
        root.add_widget(save_btn)

        # Chat display (IRC‑like scrollable)
        self.scroll = ScrollView(size_hint=(1, 0.7))
        self.chat_label = Label(
            text='',
            size_hint_y=None,
            markup=True,
            halign='left',
            valign='top'
        )
        self.chat_label.bind(texture_size=self._update_chat_height)
        self.scroll.add_widget(self.chat_label)
        root.add_widget(self.scroll)

        # Message input + send
        bottom = BoxLayout(size_hint=(1, None), height=50, spacing=5)
        self.msg_input = TextInput(hint_text='Type message...', multiline=False)
        send_btn = Button(text='Send', size_hint=(None, 1), width=80)
        send_btn.bind(on_press=self.send_message)
        bottom.add_widget(self.msg_input)
        bottom.add_widget(send_btn)
        root.add_widget(bottom)

        # Load existing messages and display
        self.refresh_chat()

        if platform == 'android':
            # Request SMS permissions at startup
            self.request_sms_permissions()
            # Start the background SMS listener service
            self.start_sms_service()

        return root

    def _update_chat_height(self, instance, value):
        self.chat_label.height = value[1]
        # Auto-scroll to bottom
        if self.scroll.scroll_y != 0:
            self.scroll.scroll_y = 0

    def save_number(self, instance):
        number = self.number_input.text.strip()
        if number:
            self.target_number = number
            self.store.put('number', number=number)
            self.refresh_chat()

    def refresh_chat(self):
        """Reload chat messages from JSON store for current number."""
        self.chat_label.text = ''
        if not self.target_number:
            return
        key = self.target_number
        if self.msg_store.exists(key):
            all_msgs = self.msg_store.get(key).get('messages', [])
            lines = []
            for msg in all_msgs:
                sender = msg.get('sender', 'me')
                body = msg.get('body', '')
                ts = msg.get('timestamp', '')
                if sender == 'me':
                    line = f"[b][color=00aa00]me[/color][/b] ({ts}): {body}"
                else:
                    line = f"[b][color=0000ff]{sender}[/color][/b] ({ts}): {body}"
                lines.append(line)
            self.chat_label.text = '\n'.join(lines)

    def send_message(self, instance):
        if not self.target_number:
            return
        text = self.msg_input.text.strip()
        if not text:
            return
        # Send SMS via Plyer (requires SEND_SMS permission)
        try:
            plyer_sms.send(recipient=self.target_number, message=text)
            # Save outgoing message locally
            self._store_message('me', text)
            self.msg_input.text = ''
            self.refresh_chat()
        except Exception as e:
            self.chat_label.text += f'\n[color=ff0000]Send failed: {e}[/color]'

    def _store_message(self, sender, body, timestamp=None):
        """Save a message to JSON under the target number."""
        if not self.target_number:
            return
        key = self.target_number
        if timestamp is None:
            timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        msg = {'sender': sender, 'body': body, 'timestamp': timestamp}
        if self.msg_store.exists(key):
            messages = self.msg_store.get(key).get('messages', [])
        else:
            messages = []
        messages.append(msg)
        self.msg_store.put(key, messages=messages)

    def request_sms_permissions(self):
        """Request necessary SMS permissions on Android (API 23+)."""
        if platform != 'android':
            return
        from android.permissions import request_permissions, Permission
        request_permissions([
            Permission.SEND_SMS,
            Permission.RECEIVE_SMS,
            Permission.READ_SMS,
            Permission.WRITE_SMS
        ])

    def start_sms_service(self):
        """Start a background service that listens for incoming SMS."""
        if platform != 'android':
            return
        try:
            service = PythonService.mService
            if not service:
                PythonService.start(mActivity, 'SmsListenerService')
                # The service class is defined below; Buildozer will load it.
        except Exception as e:
            print(f'Could not start SMS listener: {e}')

    def on_stop(self):
        # The service continues running in the background.
        pass


class SmsListenerService(PythonService):
    """Android service that continuously listens for incoming SMS."""
    SMS_RECEIVED_ACTION = 'android.provider.Telephony.SMS_RECEIVED'

    def __init__(self):
        super().__init__()
        self.receiver = None

    def start_service(self):
        if not self.receiver:
            self.receiver = _SmsBroadcastReceiver(self)
            intent_filter = IntentFilter(self.SMS_RECEIVED_ACTION)
            mActivity.registerReceiver(self.receiver, intent_filter)

    def stop_service(self):
        if self.receiver:
            mActivity.unregisterReceiver(self.receiver)
            self.receiver = None


class _SmsBroadcastReceiver(BroadcastReceiver):
    """Handles the actual SMS_RECEIVED intent."""
    def __init__(self, service):
        super().__init__()
        self.service = service

    def onReceive(self, context, intent):
        # Called in a worker thread; we schedule processing on the main thread
        Clock.schedule_once(lambda dt: self.process_sms(intent))

    def process_sms(self, intent):
        # Get the app instance
        app = App.get_running_app()
        if not app or not app.target_number:
            return
        target = app.target_number
        # Extract SMS messages from intent
        messages = []
        if intent.getAction() == SmsListenerService.SMS_RECEIVED_ACTION:
            # Use Telephony.Sms.Intents to parse
            SmsMessage = autoclass('android.telephony.SmsMessage')
            pdu_array = intent.getSerializableExtra('pdus')
            if pdu_array:
                for pdu in pdu_array:
                    pdu_bytes = bytes(pdu) if hasattr(pdu, '__bytes__') else pdu
                    sms = SmsMessage.createFromPdu(pdu_bytes)
                    sender = sms.getOriginatingAddress()
                    body = sms.getMessageBody()
                    timestamp = sms.getTimestampMillis()
                    if sender == target:
                        messages.append((sender, body, timestamp))
        # Save and schedule deletion
        for sender, body, ts in messages:
            # Save to our storage
            app._store_message(sender, body, time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts / 1000)))
            # Schedule deletion from main SMS database after 1 minute
            Clock.schedule_once(lambda dt, t=ts: schedule_deletion(t), 60)
        app.refresh_chat()


def schedule_deletion(timestamp):
    """Delete the SMS from the system SMS provider after the timeout."""
    if platform != 'android':
        return
    try:
        # Access content resolver
        resolver = mActivity.getContentResolver()
        SMS_URI = Uri.parse('content://sms')
        # Delete where date = timestamp (crude but works for this simple demo)
        selection = 'date = ?'
        args = [str(timestamp)]
        resolver.delete(SMS_URI, selection, args)
    except Exception as e:
        print(f'Deletion failed: {e}')


if __name__ == '__main__':
    ChatApp().run()
