import asyncio
import websockets
import json
import os
from datetime import datetime, timedelta
import time
from threading import Thread
import logging
import requests

# Silenciar logs da IQ Option e de bibliotecas externas
logging.basicConfig(level=logging.WARNING)
logging.getLogger('iqoptionapi').setLevel(logging.WARNING)
logging.getLogger('websockets').setLevel(logging.WARNING)
logging.getLogger('asyncio').setLevel(logging.WARNING)

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.scrollview import ScrollView
from kivy.clock import Clock
from kivy.properties import StringProperty
from kivy.core.window import Window
from kivy.utils import get_color_from_hex
from kivy.uix.popup import Popup
from kivy.graphics import Color, Rectangle
from kivy.logger import Logger
from iqoptionapi.stable_api import IQ_Option

class Colors:
    PRIMARY = '#2196F3'
    SUCCESS = '#4CAF50'
    DANGER = '#f44336'
    WARNING = '#FF9800'
    DARK = '#2b2b2b'
    LIGHT = '#f0f0f0'
    WHITE = '#ffffff'
    BLACK = '#000000'

# Validação por e-mail (Google Sheets)
SCRIPT_URL = "https://script.google.com/macros/s/AKfycbze4ZNVVfPfx8fp60HyzXR2f1ccGp-Z4zzwcKbMELBRnkwh57soz74-7qd0n7kGpjaUzw/exec"


class ClientApp(App):
    status_text = StringProperty('Status: Desconectado')
    status_color = StringProperty(Colors.DANGER)
    balance_text = StringProperty('R$ 0.00')
    wins_text = StringProperty('0')
    losses_text = StringProperty('0')
    profit_text = StringProperty('R$ 0.00')
    profit_color = StringProperty(Colors.WARNING)
    last_trade_text = StringProperty('Última operação: Aguardando...')
    last_trade_color = StringProperty(Colors.BLACK)
    current_trade_text = StringProperty('Operação atual: Nenhuma')
    current_trade_color = StringProperty(Colors.BLACK)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        
        # 🔧 CORREÇÃO PARA WINDOWS 10 (COLOCAR AQUI!)
        import sys
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
        self.cliente_config = self.carregar_configuracoes()
        self.client_id = self.cliente_config['client_id']
        self.token = self.cliente_config['token']
        self.account_type = self.cliente_config['account_type']
        self.asset_type = self.cliente_config['asset_type']
        self.entry_amount = self.cliente_config['entry_amount']
        
        self.connected = False
        self.connection = None
        self.iq_option = None
        self.loop = asyncio.new_event_loop()  # <- Depois da correção
        self.loop_thread = None
        
        self.wins = 0
        self.losses = 0
        self.total_profit = 0.0
        self.initial_balance = 0.0
        self.current_balance = 0.0
        
        self.stop_win = 0.0
        self.stop_loss = 0.0
        self.stop_win_active = False
        self.stop_loss_active = False
        
        self.current_round_id = None
        self.round_profit = 0.0
        self.round_total = 0
        self.round_done = 0
        self.round_lock = asyncio.Lock()
        
        self.log_messages = []
        self.log_widget = None

        self.current_expiration = None          # momento de expiração do round atual
        self.round_initial_balance = None       # saldo antes da primeira ordem do round

    def carregar_configuracoes(self):
        try:
            config_path = os.path.join(os.path.dirname(__file__), 'config.json')
            with open(config_path, 'r') as f:
                config = json.load(f)
            if 'clients' not in config or len(config['clients']) == 0:
                raise ValueError("Nenhum cliente encontrado no config.json")
            return config['clients'][0]
        except Exception as e:
            Logger.error(f"Erro ao carregar configurações: {e}")
            popup = Popup(title='Erro', 
                         content=Label(text='Arquivo config.json não encontrado ou inválido.'),
                         size_hint=(0.8, 0.4))
            popup.open()
            raise e





    def validar_email(self, email):
        try:
            response = requests.get(SCRIPT_URL, params={'email': email}, timeout=10)

            if response.status_code == 200:
                resultado = response.text.strip().lower()

                if resultado == "autorizado":
                    Clock.schedule_once(lambda dt:
                        self.add_message(f'✅ Licença ativa para {email}', 'success'))
                    return True
                else:
                    Clock.schedule_once(lambda dt:
                        self.add_message(f'⛔ E-mail não autorizado: {email}', 'error'))
                    return False

            Clock.schedule_once(lambda dt:
                self.add_message(f'⚠️ Erro ao validar e-mail (HTTP {response.status_code})', 'warning'))

        except Exception as e:
            err = str(e)
            Clock.schedule_once(lambda dt, msg=err:
                self.add_message(f'❌ Erro na validação do e-mail: {msg}', 'error'))

        return False







    def build(self):
        Window.minimum_width = 800
        Window.minimum_height = 600
        
        root = BoxLayout(orientation='vertical', padding=10, spacing=5)
        
        control_frame = BoxLayout(orientation='horizontal', size_hint_y=0.15, spacing=10)
        
        left_col = BoxLayout(orientation='vertical', spacing=2)
        self.connect_btn = Button(text='Conectar', background_color=get_color_from_hex(Colors.SUCCESS),
                                 on_press=self.connect_all)
        self.disconnect_btn = Button(text='Desconectar', background_color=get_color_from_hex(Colors.DANGER),
                                    on_press=self.disconnect_all, disabled=True)
        self.refresh_btn = Button(text='Atualizar Saldo', background_color=get_color_from_hex(Colors.PRIMARY),
                                 on_press=self.manual_update_balance, disabled=True)
        left_col.add_widget(self.connect_btn)
        left_col.add_widget(self.disconnect_btn)
        left_col.add_widget(self.refresh_btn)
        
        mid_col = GridLayout(cols=2, spacing=5, size_hint_x=0.4)
        mid_col.add_widget(Label(text='Banca:', bold=True, halign='left'))
        self.balance_lbl = Label(text=self.balance_text, bold=True, color=get_color_from_hex(Colors.PRIMARY),
                               halign='left')
        mid_col.add_widget(self.balance_lbl)
        mid_col.add_widget(Label(text='Wins:', bold=True, halign='left'))
        self.wins_lbl = Label(text=self.wins_text, bold=True, color=get_color_from_hex(Colors.SUCCESS),
                            halign='left')
        mid_col.add_widget(self.wins_lbl)
        mid_col.add_widget(Label(text='Loss:', bold=True, halign='left'))
        self.loss_lbl = Label(text=self.losses_text, bold=True, color=get_color_from_hex(Colors.DANGER),
                            halign='left')
        mid_col.add_widget(self.loss_lbl)
        
        right_col = GridLayout(cols=3, spacing=2, size_hint_x=0.4)
        right_col.add_widget(Label(text='Lucro:', bold=True, halign='left'))
        self.profit_lbl = Label(text=self.profit_text, bold=True, color=get_color_from_hex(Colors.WARNING),
                              halign='left')
        right_col.add_widget(self.profit_lbl)
        right_col.add_widget(Label())
        
        right_col.add_widget(Label(text='Stop Win:', halign='left'))
        self.stop_win_input = TextInput(text='0', multiline=False, size_hint_x=0.5)
        right_col.add_widget(self.stop_win_input)
        right_col.add_widget(Label(text='R$', halign='left'))
        
        right_col.add_widget(Label(text='Stop Loss:', halign='left'))
        self.stop_loss_input = TextInput(text='0', multiline=False, size_hint_x=0.5)
        right_col.add_widget(self.stop_loss_input)
        right_col.add_widget(Label(text='R$', halign='left'))
        
        self.apply_stops_btn = Button(text='Aplicar Stops', background_color=get_color_from_hex('#607D8B'),
                                     on_press=self.apply_stops, disabled=True)
        right_col.add_widget(self.apply_stops_btn)
        
        control_frame.add_widget(left_col)
        control_frame.add_widget(mid_col)
        control_frame.add_widget(right_col)
        root.add_widget(control_frame)
        
        self.status_lbl = Label(text=self.status_text, size_hint_y=0.05,
                              color=get_color_from_hex(self.status_color), bold=True)
        root.add_widget(self.status_lbl)
        
        separator = BoxLayout(size_hint_y=None, height=2)
        with separator.canvas:
            Color(*get_color_from_hex('#cccccc'))
            Rectangle(pos=separator.pos, size=separator.size)
        def update_separator(instance, _):
            instance.canvas.clear()
            with instance.canvas:
                Color(*get_color_from_hex('#cccccc'))
                Rectangle(pos=instance.pos, size=instance.size)
        separator.bind(pos=update_separator, size=update_separator)
        root.add_widget(separator)
        
        last_trade_frame = BoxLayout(orientation='vertical', size_hint_y=0.1, padding=5)
        last_trade_frame.add_widget(Label(text='Última operação:', size_hint_y=0.3))
        self.last_trade_lbl = Label(text=self.last_trade_text, size_hint_y=0.7,
                                   color=get_color_from_hex(self.last_trade_color))
        last_trade_frame.add_widget(self.last_trade_lbl)
        root.add_widget(last_trade_frame)
        
        # Comentei a linha abaixo para remover o campo "Operação atual"
        # current_trade_frame = BoxLayout(orientation='vertical', size_hint_y=0.1, padding=5)
        # ... etc.
        
        log_frame = BoxLayout(orientation='vertical', size_hint_y=0.7)
        log_frame.add_widget(Label(text='Logs', size_hint_y=0.1, bold=True))
        scroll = ScrollView(size_hint_y=0.9)
        self.log_grid = GridLayout(cols=1, size_hint_y=None, spacing=2)
        self.log_grid.bind(minimum_height=self.log_grid.setter('height'))
        scroll.add_widget(self.log_grid)
        log_frame.add_widget(scroll)
        root.add_widget(log_frame)
        
        self.start_event_loop_thread()
        Clock.schedule_interval(self.update_balance_display, 5)
        
        return root

    def start_event_loop_thread(self):
        self.loop_thread = Thread(target=self._run_event_loop, daemon=True)
        self.loop_thread.start()

    def _run_event_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def update_gui_state(self, dt=None):
        if self.connected:
            self.connect_btn.disabled = True
            self.disconnect_btn.disabled = False
            self.refresh_btn.disabled = False
            self.apply_stops_btn.disabled = False
            self.status_text = 'Status: Conectado'
            self.status_color = Colors.SUCCESS
        else:
            self.connect_btn.disabled = False
            self.disconnect_btn.disabled = True
            self.refresh_btn.disabled = True
            self.apply_stops_btn.disabled = True
            self.status_text = 'Status: Desconectado'
            self.status_color = Colors.DANGER
        self.status_lbl.color = get_color_from_hex(self.status_color)
        self.status_lbl.text = self.status_text



    def conectar_servidor(self):
        """Conecta ao servidor WebSocket usando o método assíncrono"""
        try:
            self.add_message('🔄 Conectando ao servidor: ws://191.252.38.43:8765', 'info')
            # Usa o método correto que já existe no código
            asyncio.run_coroutine_threadsafe(self.connect_to_server(), self.loop)
        except Exception as e:
            # SALVA a mensagem ANTES de usar no lambda
            error_msg = str(e)
            Clock.schedule_once(
                lambda dt, msg=error_msg: self.add_message(
                    f'❌ Erro ao conectar ao servidor: {msg}', 'error'
                )
            )
            import traceback
            traceback.print_exc()

    def on_error(self, ws, error):
        """Callback de erro do WebSocket"""
        Clock.schedule_once(lambda dt: self.add_message(f'❌ Erro WebSocket: {error}', 'error'))


    def solicitar_codigo_2fa(self):
        """
        Abre popup 2FA na THREAD PRINCIPAL do Kivy.
        Bloqueia apenas a thread de conexão.
        """
        resultado = {'codigo': None, 'done': False}

        def abrir_popup(dt):
            layout = BoxLayout(orientation='vertical', spacing=10, padding=10)
            input_codigo = TextInput(multiline=False, hint_text='Digite o código 2FA')

            btn_layout = BoxLayout(size_hint_y=0.4, spacing=10)
            btn_ok = Button(text='Confirmar')
            btn_cancel = Button(text='Cancelar')

            popup = Popup(
                title='Autenticação 2FA',
                content=layout,
                size_hint=(0.6, 0.4),
                auto_dismiss=False
            )

            def confirmar(instance):
                resultado['codigo'] = input_codigo.text.strip()
                resultado['done'] = True
                popup.dismiss()

            def cancelar(instance):
                resultado['done'] = True
                popup.dismiss()

            btn_ok.bind(on_press=confirmar)
            btn_cancel.bind(on_press=cancelar)

            layout.add_widget(Label(text='📩 Digite o código 2FA recebido:'))
            layout.add_widget(input_codigo)
            btn_layout.add_widget(btn_ok)
            btn_layout.add_widget(btn_cancel)
            layout.add_widget(btn_layout)

            popup.open()

        # GARANTE execução na thread principal
        Clock.schedule_once(abrir_popup)

        # espera usuário responder (apenas esta thread bloqueia)
        while not resultado['done']:
            time.sleep(0.1)

        return resultado['codigo']








    def connect_all(self, instance):
        if self.connected:
            self.add_message('⚠️ Já está conectado', 'warning')
            return
        self.add_message('🔄 Iniciando conexão completa...', 'info')
        Thread(target=self._connect_iq_option_thread).start()

    def _connect_iq_option_thread(self):
        success = self.connect_to_iq_option()
        if success:
            asyncio.run_coroutine_threadsafe(self.connect_to_server(), self.loop)
        else:
            Clock.schedule_once(lambda dt: self.add_message('❌ Conexão com IQ Option falhou. Verifique config.json', 'error'))

    def get_credentials(self):
        return self.cliente_config['email'], self.cliente_config['password']











    def connect_to_iq_option(self):
        try:
            email, pwd = self.get_credentials()

            # ---------- VALIDAÇÃO POR EMAIL ----------
            Clock.schedule_once(lambda dt:
                self.add_message('🔎 Validando licença por e-mail...', 'info'))

            if not self.validar_email(email):
                Clock.schedule_once(lambda dt:
                    self.add_message('❌ Conexão bloqueada: e-mail não autorizado', 'error'))
                return False
            # ---------- FIM VALIDAÇÃO ----------

            self.iq_option = IQ_Option(email, pwd)

            Clock.schedule_once(lambda dt:
                self.add_message('🔄 Conectando à IQ Option...', 'info'))

            success, reason = self.iq_option.connect()

            if not success:

                # ---------- NOVO BLOCO 2FA ----------
                if reason and "2fa" in reason.lower():
                    Clock.schedule_once(lambda dt:
                        self.add_message('🔐 Autenticação 2FA necessária', 'warning'))

                    codigo_2fa = self.solicitar_codigo_2fa()

                    if not codigo_2fa:
                        Clock.schedule_once(lambda dt:
                            self.add_message('❌ Código 2FA não informado', 'error'))
                        return False

                    check_2fa, reason_2fa = self.iq_option.connect_2fa(codigo_2fa)

                    if not check_2fa:
                        Clock.schedule_once(lambda dt:
                            self.add_message(f'❌ Falha no 2FA: {str(reason_2fa)}', 'error'))
                        return False
                    else:
                        Clock.schedule_once(lambda dt:
                            self.add_message('✅ Autenticação 2FA concluída', 'success'))
                else:
                    Clock.schedule_once(lambda dt:
                        self.add_message(f'❌ Falha na conexão: {reason}', 'error'))
                    return False
                # ---------- FIM BLOCO 2FA ----------


            time.sleep(2)

            if self.iq_option.check_connect():
                self.initial_balance = self.iq_option.get_balance()
                self.current_balance = self.initial_balance

                if self.account_type.lower() == 'real':
                    self.iq_option.change_balance('REAL')
                    acc_type = 'REAL'
                else:
                    self.iq_option.change_balance('PRACTICE')
                    acc_type = 'PRÁTICA'

                Clock.schedule_once(lambda dt:
                    self.add_message(f'✅ Conectado à IQ Option - Conta {acc_type}', 'success'))

                Clock.schedule_once(lambda dt:
                    self.add_message(f'💰 Saldo inicial: R$ {self.initial_balance:.2f}', 'info'))

                Clock.schedule_once(lambda dt: self.update_balance_display())
                return True

            Clock.schedule_once(lambda dt:
                self.add_message('❌ Falha na conexão com IQ Option.', 'error'))
            return False

        except Exception as e:
            err = str(e)
            Clock.schedule_once(lambda dt, msg=err:
                self.add_message(f'❌ Erro na conexão IQ: {msg}', 'error'))
            return False



    async def connect_to_server(self):
        try:
            uri = 'ws://191.252.38.43:8765'
            Clock.schedule_once(lambda dt: self.add_message(f'🔄 Conectando ao servidor: {uri}', 'info'))
            
            # Conecta com timeout e ping_interval
            self.connection = await websockets.connect(
                uri, 
                ping_interval=20,
                ping_timeout=60,
                close_timeout=10
            )
            
            Clock.schedule_once(lambda dt: self.add_message('✅ Conectado ao servidor WebSocket', 'success'))
            
            # ENVIA AUTENTICAÇÃO IMEDIATAMENTE
            await self.send_initial_order()
            
            self.connected = True
            Clock.schedule_once(self.update_gui_state)
            
            # Inicia a escuta de mensagens
            asyncio.create_task(self.listen_for_messages())
            
        except Exception as e:
            error_msg = str(e)
            Clock.schedule_once(lambda dt, msg=error_msg: 
                self.add_message(f'❌ Erro ao conectar ao servidor: {msg}', 'error'))
            self.connected = False
            Clock.schedule_once(self.update_gui_state)

    async def send_initial_order(self):
        auth = {'client_id': self.client_id, 'token': self.token}
        await self.connection.send(json.dumps(auth))
        Clock.schedule_once(lambda dt: self.add_message(f'📤 Autenticação enviada: {self.client_id}', 'info'))

    async def listen_for_messages(self):
        try:
            async for message in self.connection:
                Clock.schedule_once(lambda dt, m=message: self.add_message(f'📥 Recebido: {m}', 'info'))
                data = json.loads(message)
                if data.get('status') == 'connected':
                    Clock.schedule_once(lambda dt, d=data: self.add_message(f"✅ Autenticado no servidor como {d['client_id']}", 'success'))
                    continue
                if 'type' in data and 'ativo' in data and 'tempo' in data:
                    Clock.schedule_once(lambda dt, d=data: self.add_message(f"🎯 Nova ordem recebida: {d['type']} {d['ativo']} {d['tempo']}min", 'trade'))
                    round_id = self.get_round_id()
                    if self.current_round_id != round_id:
                        self.current_round_id = round_id
                        self.round_profit = 0.0
                        self.round_total = 0
                        self.round_done = 0
                    self.round_total += 1
                    if data['type'] == 'buy':
                        asyncio.create_task(self.execute_trade_order(data['ativo'], 'call', data['tempo']))
                    elif data['type'] == 'sell':
                        asyncio.create_task(self.execute_trade_order(data['ativo'], 'put', data['tempo']))
        except websockets.ConnectionClosed:
            Clock.schedule_once(lambda dt: self.add_message('❌ Conexão WebSocket fechada', 'error'))
            self.connected = False
            Clock.schedule_once(self.update_gui_state)
        except Exception as e:
            Clock.schedule_once(lambda dt: self.add_message(f'❌ Erro ao ouvir mensagens: {str(e)}', 'error'))

    def get_round_id(self):
        return datetime.now().strftime('%Y%m%d%H%M')

    # ---------- NOVO MÉTODO: calcular expiração ----------
    def calcular_expiracao(self, tempo_vela):
        """
        Retorna o timestamp (datetime) de quando a operação deve ser verificada,
        considerando a duração da vela e o segundo da compra.
        """
        agora = datetime.now()
        segundo = agora.second
        micro = agora.microsecond

        # Para simplificar, tratamos apenas durações de 1, 5 e 15 minutos
        if tempo_vela == 1:
            deadline_segundo = 29
            # Início do minuto atual
            inicio_periodo = agora.replace(second=0, microsecond=0)
            fim_periodo = inicio_periodo + timedelta(minutes=1)

            if segundo <= deadline_segundo:
                # Compra dentro da janela → expira no final deste minuto
                expiracao_tecnica = fim_periodo
            else:
                # Compra após deadline → expira no final do próximo minuto
                expiracao_tecnica = fim_periodo + timedelta(minutes=1)

        elif tempo_vela == 5:
            # Períodos de 5 minutos: 00,05,10,... até 55
            minuto_atual = agora.minute
            # Encontra o início do período de 5 minutos atual
            inicio_minuto_periodo = (minuto_atual // 5) * 5
            inicio_periodo = agora.replace(minute=inicio_minuto_periodo, second=0, microsecond=0)
            fim_periodo = inicio_periodo + timedelta(minutes=5)
            # Deadline: últimos 30 segundos do período
            deadline = fim_periodo - timedelta(seconds=30)

            if agora < deadline:
                # Compra antes do deadline → expira no final deste período
                expiracao_tecnica = fim_periodo
            else:
                # Compra no deadline ou depois → expira no final do próximo período
                expiracao_tecnica = fim_periodo + timedelta(minutes=5)

        elif tempo_vela == 15:
            # Períodos de 15 minutos: 00,15,30,45
            minuto_atual = agora.minute
            inicio_minuto_periodo = (minuto_atual // 15) * 15
            inicio_periodo = agora.replace(minute=inicio_minuto_periodo, second=0, microsecond=0)
            fim_periodo = inicio_periodo + timedelta(minutes=15)
            # Trava de 5 minutos antes do fim (segundo as regras que você descreveu)
            deadline = fim_periodo - timedelta(minutes=5)

            if agora < deadline:
                expiracao_tecnica = fim_periodo
            else:
                expiracao_tecnica = fim_periodo + timedelta(minutes=15)
        else:
            # Para outras durações (ex: 30min, 1h), usa lógica simples: expira após 'tempo_vela' minutos
            expiracao_tecnica = agora + timedelta(minutes=tempo_vela)

        # Adiciona 25 segundos para processamento da IQ Option (ajuste se necessário)
        momento_verificacao = expiracao_tecnica + timedelta(seconds=3)
        return momento_verificacao

    # ---------- MÉTODO CORRIGIDO execute_trade_order ----------
    async def execute_trade_order(self, ativo, direction, tempo):
        try:
            # Calcula o momento de expiração (já está correto no seu código)
            expiration_time = self.calcular_expiracao(tempo)

            # Bloco protegido para verificar se já existe um round ativo com esta expiração
            async with self.round_lock:
                if (hasattr(self, 'current_expiration') and 
                    self.current_expiration == expiration_time and 
                    self.round_initial_balance is not None):
                    # Já temos uma verificação agendada para esta expiração
                    primeira_ordem = False
                else:
                    # Primeira ordem para esta expiração
                    primeira_ordem = True
                    self.current_expiration = expiration_time
                    self.round_initial_balance = self.current_balance  # saldo antes da compra

            # Executa a ordem (fora do lock)
            Clock.schedule_once(lambda dt, a=ativo:
                self._update_last_trade(f'Última operação: {a} - ⏳ Pendente', Colors.WARNING))
            Clock.schedule_once(lambda dt, a=ativo:
                self.add_message(f'⏳ Operação {a} iniciada - Aguardando resultado...', 'pending'))

            if self.asset_type.lower() == 'digital':
                status, result = self.iq_option.buy_digital_spot(ativo, self.entry_amount, direction, tempo)
            else:
                status, result = self.iq_option.buy(self.entry_amount, ativo, direction, tempo)

            if not status:
                Clock.schedule_once(lambda dt, a=ativo: self.add_message(f'❌ Falha ao executar ordem: {a}', 'error'))
                # Se foi a primeira ordem, reseta o round
                if primeira_ordem:
                    async with self.round_lock:
                        self.current_expiration = None
                        self.round_initial_balance = None
                # Não contabiliza win/loss para falha de execução
                return

            # Sucesso na execução
            Clock.schedule_once(lambda dt, a=ativo, d=direction, t=tempo:
                self.add_message(f'✅ Ordem executada: {a} {d} | Valor: R$ {self.entry_amount} | Tempo: {t}min', 'success'))

            # Se foi a primeira ordem, agenda a verificação consolidada
            if primeira_ordem:
                asyncio.create_task(self._verificar_round(ativo, expiration_time))

        except Exception as e:
            Clock.schedule_once(lambda dt, err=e: self.add_message(f'❌ Erro ao processar ordem: {str(err)}', 'error'))
            # Em caso de exceção, se era a primeira ordem, reseta o round
            async with self.round_lock:
                if hasattr(self, 'current_expiration') and self.current_expiration is not None:
                    self.current_expiration = None
                    self.round_initial_balance = None

    async def _verificar_round(self, ativo, expiration_time):
        try:
            # Calcula segundos até a expiração
            agora = datetime.now()
            segundos_ate = (expiration_time - agora).total_seconds()
            if segundos_ate < 0:
                segundos_ate = 0

            # Aguarda até o momento exato
            await asyncio.sleep(segundos_ate)

            # Atualiza saldo (duas vezes para garantir)
            self.update_balance_display_sync()
            await asyncio.sleep(2)
            self.update_balance_display_sync()

            # Bloqueia para acessar as variáveis do round
            async with self.round_lock:
                # Verifica se ainda é o round esperado (pode ter sido resetado)
                if not hasattr(self, 'current_expiration') or self.current_expiration != expiration_time:
                    return  # Round já foi processado ou cancelado

                # Compara saldo atual com o saldo inicial do round
                if self.current_balance > self.round_initial_balance:
                    self.wins += 1
                    profit = self.current_balance - self.round_initial_balance
                    Clock.schedule_once(lambda dt, a=ativo, p=profit:
                        self.add_message(f'💰 WIN CONSOLIDADO! {a} - Lucro total: R$ {p:.2f}', 'profit'))
                    Clock.schedule_once(lambda dt, a=ativo, p=profit:
                        self._update_last_trade(f'Última operação: {a} - ✅ WIN (+R$ {p:.2f})', Colors.SUCCESS))
                elif self.current_balance < self.round_initial_balance:
                    self.losses += 1
                    loss = self.round_initial_balance - self.current_balance
                    Clock.schedule_once(lambda dt, a=ativo, l=loss:
                        self.add_message(f'💸 LOSS CONSOLIDADO! {a} - Perda total: R$ {l:.2f}', 'loss'))
                    Clock.schedule_once(lambda dt, a=ativo, l=loss:
                        self._update_last_trade(f'Última operação: {a} - ❌ LOSS (-R$ {l:.2f})', Colors.DANGER))
                else:
                    Clock.schedule_once(lambda dt, a=ativo:
                        self.add_message(f'❓ Resultado indeterminado para {a}', 'warning'))
                    Clock.schedule_once(lambda dt, a=ativo:
                        self._update_last_trade(f'Última operação: {a} - ⚠️ Indeterminado', Colors.WARNING))

                # Atualiza estatísticas
                Clock.schedule_once(lambda dt: self._update_stats())

                # Reseta o round
                self.current_expiration = None
                self.round_initial_balance = None

        except Exception as e:
            Clock.schedule_once(lambda dt, err=e: self.add_message(f'❌ Erro na verificação do round: {str(err)}', 'error'))
            async with self.round_lock:
                self.current_expiration = None
                self.round_initial_balance = None

    # ---------- MÉTODOS AUXILIARES (inalterados) ----------
    def _update_last_trade(self, text, color_hex):
        self.last_trade_text = text
        self.last_trade_color = color_hex
        self.last_trade_lbl.text = text
        self.last_trade_lbl.color = get_color_from_hex(color_hex)

    def _update_stats(self):
        self.wins_text = str(self.wins)
        self.losses_text = str(self.losses)
        self.wins_lbl.text = self.wins_text
        self.loss_lbl.text = self.losses_text

    def update_balance_display_sync(self):
        if self.iq_option and self.iq_option.check_connect():
            balance = self.iq_option.get_balance()
            if balance is not None:
                self.current_balance = balance
                self.total_profit = self.current_balance - self.initial_balance
                Clock.schedule_once(lambda dt: self._refresh_balance_ui())

    def update_balance_display(self, dt=None):
        if self.connected and self.iq_option and self.iq_option.check_connect():
            self.update_balance_display_sync()

    def _refresh_balance_ui(self):
        self.balance_text = f'R$ {self.current_balance:.2f}'
        self.balance_lbl.text = self.balance_text
        profit_text = f'R$ {self.total_profit:+.2f}'
        if self.total_profit > 0:
            self.profit_color = Colors.SUCCESS
        elif self.total_profit < 0:
            self.profit_color = Colors.DANGER
        else:
            self.profit_color = Colors.WARNING
        self.profit_text = profit_text
        self.profit_lbl.text = profit_text
        self.profit_lbl.color = get_color_from_hex(self.profit_color)
        self.check_stops()

    def manual_update_balance(self, instance):
        if self.connected and self.iq_option:
            self.update_balance_display_sync()
            self.add_message('🔄 Saldo atualizado manualmente', 'info')

    def check_stops(self):
        if self.stop_win_active and self.total_profit >= self.stop_win:
            self.add_message(f'🎯 STOP WIN ATINGIDO! Lucro: R$ {self.total_profit:.2f}', 'profit')
            self._update_last_trade(f'🎯 STOP WIN ATINGIDO! Lucro: R$ {self.total_profit:.2f}', Colors.SUCCESS)
            self.show_popup('Stop Win', f'Stop Win atingido!\nLucro total: R$ {self.total_profit:.2f}')
            self.disconnect_all(None)
        elif self.stop_loss_active and self.total_profit <= -self.stop_loss:
            self.add_message(f'⚠️ STOP LOSS ATINGIDO! Prejuízo: R$ {-self.total_profit:.2f}', 'loss')
            self._update_last_trade(f'⚠️ STOP LOSS ATINGIDO! Prejuízo: R$ {-self.total_profit:.2f}', Colors.DANGER)
            self.show_popup('Stop Loss', f'Stop Loss atingido!\nPrejuízo total: R$ {-self.total_profit:.2f}')
            self.disconnect_all(None)

    def show_popup(self, title, message):
        popup = Popup(title=title, content=Label(text=message), size_hint=(0.6, 0.4))
        popup.open()

    def disconnect_all(self, instance):
        if not self.connected:
            self.add_message('⚠️ Já está desconectado', 'warning')
            return
        self.add_message('🔄 Desconectando...', 'info')
        if self.connection:
            asyncio.run_coroutine_threadsafe(self.connection.close(), self.loop)
            self.connection = None
        if self.iq_option:
            try:
                self.iq_option.close_websocket()
            except:
                pass
        self.connected = False
        Clock.schedule_once(self.update_gui_state)
        self.add_message('✅ Desconectado com sucesso', 'success')

    def apply_stops(self, instance):
        try:
            self.stop_win = float(self.stop_win_input.text)
            self.stop_loss = float(self.stop_loss_input.text)
            self.stop_win_active = self.stop_win > 0
            self.stop_loss_active = self.stop_loss > 0
            if self.stop_win_active and self.stop_loss_active:
                self.add_message(f'✅ Stops aplicados: Win R$ {self.stop_win:.2f} | Loss R$ {self.stop_loss:.2f}', 'success')
            elif self.stop_win_active:
                self.add_message(f'✅ Stop Win aplicado: R$ {self.stop_win:.2f}', 'success')
            elif self.stop_loss_active:
                self.add_message(f'✅ Stop Loss aplicado: R$ {self.stop_loss:.2f}', 'success')
            else:
                self.add_message('ℹ️ Stops desativados', 'info')
        except ValueError:
            self.add_message('❌ Valores inválidos para stops. Use números (ex: 100.50)', 'error')

    def add_message(self, message, tag='info'):
        timestamp = datetime.now().strftime('%H:%M:%S')
        formatted = f'[{timestamp}] {message}'
        color = {
            'success': Colors.SUCCESS,
            'error': Colors.DANGER,
            'info': Colors.PRIMARY,
            'warning': Colors.WARNING,
            'profit': Colors.SUCCESS,
            'loss': Colors.DANGER,
            'trade': '#FFD700',
            'pending': '#9C27B0'
        }.get(tag, Colors.BLACK)
        lbl = Label(text=formatted, size_hint_y=None, height=20, color=get_color_from_hex(color),
                   halign='left', valign='middle', text_size=(self.log_grid.width-10, None))
        lbl.bind(size=lbl.setter('text_size'))
        self.log_grid.add_widget(lbl)
        if len(self.log_grid.children) > 100:
            self.log_grid.remove_widget(self.log_grid.children[-1])
        Logger.info(f'LOG: {formatted}')

    def on_stop(self):
        if self.connected:
            self.disconnect_all(None)
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)

if __name__ == '__main__':
    ClientApp().run()