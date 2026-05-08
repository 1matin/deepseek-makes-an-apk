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

if platform == 'android':
    from jnius import autoclass
    from android import mActivity
    from android.broadcast import BroadcastReceiver

    PythonService = autoclass('org.kivy.android.PythonService')
    Intent = autoclass('android.content.Intent')
    IntentFilter = autoclass('android.content.IntentFilter')
    Uri = autoclass('android.net.Uri')

class ChatApp(App):
    number_file = 'target_number.json'
    messages_store_file = 'messages.json'

    def build(self):
        self.store = JsonStore(self.number_file)
        self.msg_store = JsonStore(self.messages_store_file)

        # Load saved numbers
        self.target_number = self.store.get('number') if self.store.exists('number') else None
        self.medium_number = self.store.get('medium_number') if self.store.exists('medium_number') else None

        root = BoxLayout(orientation='vertical', padding=10, spacing=10)

        # --- Target number field ---
        self.number_input = TextInput(
            hint_text='Enter target phone number',
            text=self.target_number if self.target_number else '',
            multiline=False,
            size_hint=(1, None),
            height=50
        )
        root.add_widget(self.number_input)

        save_target_btn = Button(text='Save Target', size_hint=(1, None), height=40)
        save_target_btn.bind(on_press=self.save_target_number)
        root.add_widget(save_target_btn)

        # --- Medium number field ---
        self.medium_input = TextInput(
            hint_text='Enter medium proxy number',
            text=self.medium_number if self.medium_number else '',
            multiline=False,
            size_hint=(1, None),
            height=50
        )
        root.add_widget(self.medium_input)

        save_medium_btn = Button(text='Save Medium', size_hint=(1, None), height=40)
        save_medium_btn.bind(on_press=self.save_medium_number)
        root.add_widget(save_medium_btn)

        # Chat display (IRC‑like, scrollable)
        self.scroll = ScrollView(size_hint=(1, 0.6))
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

        # Load existing messages
        self.refresh_chat()

        if platform == 'android':
            self.request_sms_permissions()
            self.start_sms_service()

        return root

    def _update_chat_height(self, instance, value):
        self.chat_label.height = value[1]
        if self.scroll.scroll_y != 0:
            self.scroll.scroll_y = 0

    def save_target_number(self, instance):
        number = self.number_input.text.strip()
        if number:
            self.target_number = number
            self.store.put('number', number=number)
            self.refresh_chat()

    def save_medium_number(self, instance):
        number = self.medium_input.text.strip()
        if number:
            self.medium_number = number
            self.store.put('medium_number', medium_number=number)

    def refresh_chat(self):
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
        """Send an SMS to the target number and schedule its removal."""
        if not self.target_number:
            return
        text = self.msg_input.text.strip()
        if not text:
            return
        send_time = int(time.time() * 1000)           # timestamp for later deletion
        try:
            plyer_sms.send(recipient=self.target_number, message=text)
            self._store_message('me', text)
            self.msg_input.text = ''
            self.refresh_chat()
            # Schedule deletion of this outgoing SMS from the Sent folder
            Clock.schedule_once(
                lambda dt, ts=send_time: self._delete_sms_by_time(ts, 'sent'), 60
            )
        except Exception as e:
            self.chat_label.text += f'\n[color=ff0000]Send failed: {e}[/color]'

    def _store_message(self, sender, body, timestamp=None):
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
        if platform != 'android':
            return
        try:
            service = PythonService.mService
            if not service:
                PythonService.start(mActivity, 'SmsListenerService')
        except Exception as e:
            print(f'Could not start SMS listener: {e}')

    def on_stop(self):
        pass

    @staticmethod
    def _delete_sms_by_time(timestamp, folder='inbox'):
        """Delete an SMS from the given folder (inbox/sent) using the date field."""
        if platform != 'android':
            return
        try:
            resolver = mActivity.getContentResolver()
            uri = Uri.parse(f'content://sms/{folder}')
            selection = 'date = ?'
            args = [str(timestamp)]
            resolver.delete(uri, selection, args)
        except Exception as e:
            print(f'Deletion failed from {folder}: {e}')


class SmsListenerService(PythonService):
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
    def __init__(self, service):
        super().__init__()
        self.service = service

    def onReceive(self, context, intent):
        Clock.schedule_once(lambda dt: self.process_sms(intent))

    def process_sms(self, intent):
        app = App.get_running_app()
        if not app:
            return

        target = app.target_number
        medium = app.medium_number

        if intent.getAction() != SmsListenerService.SMS_RECEIVED_ACTION:
            return

        SmsMessage = autoclass('android.telephony.SmsMessage')
        pdu_array = intent.getSerializableExtra('pdus')
        if not pdu_array:
            return

        for pdu in pdu_array:
            pdu_bytes = bytes(pdu) if hasattr(pdu, '__bytes__') else pdu
            sms = SmsMessage.createFromPdu(pdu_bytes)
            sender = sms.getOriginatingAddress()
            body = sms.getMessageBody()
            timestamp = sms.getTimestampMillis()
            ts_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp / 1000))

            # 1. Incoming from target → save, delete from inbox, forward to medium
            if target and sender == target:
                app._store_message(sender, body, ts_str)
                Clock.schedule_once(
                    lambda dt, ts=timestamp: ChatApp._delete_sms_by_time(ts, 'inbox'), 60
                )
                app.refresh_chat()

                if medium:
                    self._forward_and_delete(medium, body)

            # 2. Incoming from medium → forward to target (proxy) & delete outgoing copy
            elif medium and sender == medium:
                if target:
                    self._forward_and_delete(target, body)

    def _forward_and_delete(self, recipient, message):
        """Send an SMS to `recipient` and schedule its removal from Sent folder."""
        send_time = int(time.time() * 1000)
        try:
            plyer_sms.send(recipient=recipient, message=message)
            Clock.schedule_once(
                lambda dt, ts=send_time: ChatApp._delete_sms_by_time(ts, 'sent'), 60
            )
        except Exception as e:
            print(f'Forwarding SMS failed: {e}')


if __name__ == '__main__':
    ChatApp().run()
