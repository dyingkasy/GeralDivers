import os
import sys
import json
import requests
import hashlib
from PyQt5 import QtWidgets, QtCore, QtGui
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QMessageBox, QInputDialog, QFileDialog, QProgressBar,
    QAction, QLabel, QLineEdit, QStyle, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QSpacerItem, QSizePolicy
)
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import Qt, QObject, pyqtSignal, QThread, QMutex

# Caminhos dos arquivos
DRIVERS_FILE = 'drivers.json'
DOWNLOADS_DIR = 'downloads'

# Garantir que o diretório de downloads exista
if not os.path.exists(DOWNLOADS_DIR):
    os.makedirs(DOWNLOADS_DIR)

# Drivers predefinidos (com grupos)
DEFAULT_DRIVERS = [
    {
        "nome": "POS-80",
        "url": "https://baixesoft.com/servidor_download_drivers/POS-Printer-Driver.exe",
        "grupo": "Não Fiscal",
        "checksum": ""
    },
    {
        "nome": "Epson TM-T20/TM-T20X",
        "url": "https://www.bztech.com.br/arquivos/driver-epson-tm-t20-tm-t20x.zip",
        "grupo": "Não Fiscal",
        "checksum": ""
    },
    {
        "nome": "Elgin",
        "url": "https://www.bztech.com.br/arquivos/driver-elgin-i7-i8-e-i9-windows-e-linux.zip",
        "grupo": "Não Fiscal",
        "checksum": ""
    },
    {
        "nome": "Bematech",
        "url": "https://www.bztech.com.br/arquivos/driver-bematech-mp-4200.zip",
        "grupo": "A4",
        "checksum": ""
    }
]

# Função para carregar drivers do JSON
def carregar_drivers():
    if not os.path.exists(DRIVERS_FILE):
        salvar_drivers(DEFAULT_DRIVERS)
        return DEFAULT_DRIVERS.copy()
    with open(DRIVERS_FILE, 'r', encoding='utf-8') as f:
        try:
            drivers = json.load(f)
            # Se o arquivo estiver vazio, adicionar os drivers padrão
            if not drivers:
                salvar_drivers(DEFAULT_DRIVERS)
                return DEFAULT_DRIVERS.copy()
            return drivers
        except json.JSONDecodeError:
            # Se o JSON estiver corrompido, reescreve com os drivers padrão
            salvar_drivers(DEFAULT_DRIVERS)
            return DEFAULT_DRIVERS.copy()

# Função para salvar drivers no JSON
def salvar_drivers(drivers):
    with open(DRIVERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(drivers, f, indent=4, ensure_ascii=False)

# Função para calcular checksum
def calcular_checksum(file_path, hash_type='sha256'):
    hash_func = getattr(hashlib, hash_type)()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_func.update(chunk)
    return hash_func.hexdigest()

# Classe Worker para download
class DownloadWorker(QObject):
    progress_changed = pyqtSignal(int, int)  # (download_id, progress)
    download_finished = pyqtSignal(int, bool, str)  # (download_id, success, message)

    def __init__(self, download_id, driver, save_path, priority=1):
        super().__init__()
        self.download_id = download_id
        self.driver = driver
        self.save_path = save_path
        self.priority = priority
        self._is_paused = False
        self._is_canceled = False
        self._mutex = QMutex()

    def run(self):
        try:
            # Primeiro, verificar se o servidor suporta Range requests
            head_resp = requests.head(self.driver['url'], allow_redirects=True)
            accept_ranges = head_resp.headers.get('Accept-Ranges', 'none').lower()

            supports_range = accept_ranges == 'bytes'

            headers = {}
            existing_size = 0
            if os.path.exists(self.save_path):
                existing_size = os.path.getsize(self.save_path)
                if supports_range:
                    headers['Range'] = f'bytes={existing_size}-'
                else:
                    # Se o servidor não suporta Range, deletar o arquivo existente
                    os.remove(self.save_path)
                    existing_size = 0

            response = requests.get(self.driver['url'], stream=True, headers=headers, allow_redirects=True)
            if response.status_code == 416:
                # Requested Range Not Satisfiable
                # Isso pode ocorrer se o arquivo já foi completamente baixado
                # Ou se o Range solicitado está fora dos limites
                # Nesse caso, deletar o arquivo e tentar novamente
                os.remove(self.save_path)
                response = requests.get(self.driver['url'], stream=True, allow_redirects=True)
                existing_size = 0
                if response.status_code != 200:
                    raise requests.exceptions.RequestException(f"Erro ao baixar o driver: {response.status_code} {response.reason}")

            response.raise_for_status()

            total_length = response.headers.get('content-length')
            if total_length is not None:
                total_length = int(total_length) + existing_size
            else:
                total_length = None

            mode = 'ab' if existing_size > 0 else 'wb'
            with open(self.save_path, mode) as f:
                dl = existing_size
                for data in response.iter_content(chunk_size=4096):
                    if self._is_canceled:
                        self.download_finished.emit(
                            self.download_id,
                            False,
                            f"Download do driver '{self.driver['nome']}' cancelado pelo usuário."
                        )
                        return
                    while self._is_paused:
                        QtCore.QThread.msleep(100)
                        if self._is_canceled:
                            self.download_finished.emit(
                                self.download_id,
                                False,
                                f"Download do driver '{self.driver['nome']}' cancelado pelo usuário."
                            )
                            return
                    if not data:
                        break
                    f.write(data)
                    dl += len(data)
                    if total_length:
                        done = int(100 * dl / total_length)
                        self.progress_changed.emit(self.download_id, done)
            # Verificar checksum se disponível
            if 'checksum' in self.driver and self.driver['checksum']:
                downloaded_checksum = calcular_checksum(self.save_path, 'sha256')
                if downloaded_checksum.lower() == self.driver['checksum'].lower():
                    self.download_finished.emit(
                        self.download_id,
                        True,
                        f"Driver '{self.driver['nome']}' baixado e verificado com sucesso!"
                    )
                else:
                    os.remove(self.save_path)
                    self.download_finished.emit(
                        self.download_id,
                        False,
                        f"Checksum inválido para o driver '{self.driver['nome']}'. O arquivo foi removido."
                    )
            else:
                self.download_finished.emit(
                    self.download_id,
                    True,
                    f"Driver '{self.driver['nome']}' baixado com sucesso!"
                )
        except requests.exceptions.RequestException as e:
            self.download_finished.emit(
                self.download_id,
                False,
                f"Erro ao baixar o driver: {e}"
            )

    def pause(self):
        self._mutex.lock()
        self._is_paused = True
        self._mutex.unlock()

    def resume(self):
        self._mutex.lock()
        self._is_paused = False
        self._mutex.unlock()

    def cancel(self):
        self._mutex.lock()
        self._is_canceled = True
        self._mutex.unlock()

# Classe Principal da Aplicação
class DriverDownloaderApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Driver Downloader")
        self.setGeometry(100, 100, 1200, 700)
        self.setWindowIcon(QIcon.fromTheme("application-exit"))
        self.drivers = carregar_drivers()
        self.download_id_counter = 0
        self.current_threads = {}
        self.current_workers = {}
        self.init_ui()
        self.atualizar_table()  # Chamada para popular a tabela com os drivers padrão
        self.setAcceptDrops(True)  # Habilitar Drag and Drop

    def init_ui(self):
        # Menu Bar
        menubar = self.menuBar()

        # Arquivo Menu
        file_menu = menubar.addMenu('Arquivo')

        export_action = QAction('Exportar Drivers', self)
        export_action.triggered.connect(self.exportar_drivers)
        export_action.setShortcut('Ctrl+E')
        file_menu.addAction(export_action)

        import_action = QAction('Importar Drivers', self)
        import_action.triggered.connect(self.importar_drivers)
        import_action.setShortcut('Ctrl+I')
        file_menu.addAction(import_action)

        # Configurações Menu
        settings_menu = menubar.addMenu('Configurações')

        theme_action = QAction('Tema Escuro', self, checkable=True)
        theme_action.triggered.connect(self.toggle_theme)
        settings_menu.addAction(theme_action)

        # Ajuda Menu
        help_menu = menubar.addMenu('Ajuda')

        credits_action = QAction(QIcon.fromTheme("help-about"), 'Créditos', self)
        credits_action.triggered.connect(self.show_credits)
        help_menu.addAction(credits_action)

        # Widget Central
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # Layout Principal
        main_layout = QVBoxLayout()
        central_widget.setLayout(main_layout)

        # Título
        title_label = QLabel("Painel de Download de Drivers de Impressora")
        title_font = QtGui.QFont()
        title_font.setPointSize(20)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("color: #2E8B57;")
        title_label.setAccessibleName("Título da Aplicação")
        title_label.setAccessibleDescription("Título que indica o propósito da aplicação")
        main_layout.addWidget(title_label)

        # Barra de Pesquisa
        search_layout = QHBoxLayout()
        main_layout.addLayout(search_layout)

        search_label = QLabel("Buscar:")
        search_label.setAccessibleName("Rótulo de Busca")
        search_label.setAccessibleDescription("Campo para buscar drivers por nome ou grupo")
        search_layout.addWidget(search_label)

        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Digite o nome do driver ou grupo...")
        self.search_bar.textChanged.connect(self.filtrar_drivers)
        self.search_bar.setAccessibleName("Campo de Busca")
        self.search_bar.setAccessibleDescription("Digite aqui para filtrar a lista de drivers")
        search_layout.addWidget(self.search_bar)

        # Botões Adicionar e Prioridade
        manage_layout = QHBoxLayout()
        main_layout.addLayout(manage_layout)

        add_button = QPushButton("Adicionar Driver")
        add_button.setIcon(QIcon.fromTheme("list-add"))
        add_button.clicked.connect(self.adicionar_driver)
        add_button.setStyleSheet("padding: 10px;")
        add_button.setShortcut('Ctrl+A')
        add_button.setAccessibleName("Botão Adicionar Driver")
        add_button.setAccessibleDescription("Clique para adicionar um novo driver")
        manage_layout.addWidget(add_button)

        # ComboBox para Gerenciamento Avançado de Downloads
        priority_label = QLabel("Prioridade do Download:")
        priority_label.setAccessibleName("Rótulo Prioridade")
        priority_label.setAccessibleDescription("Selecione a prioridade para o download")
        manage_layout.addWidget(priority_label)

        self.priority_combo = QComboBox()
        self.priority_combo.addItems(["Baixa", "Média", "Alta"])
        self.priority_combo.setCurrentIndex(1)  # Média como padrão
        self.priority_combo.setAccessibleName("ComboBox Prioridade")
        self.priority_combo.setAccessibleDescription("Escolha a prioridade para o download")
        manage_layout.addWidget(self.priority_combo)

        # Spacer para alinhar os elementos à esquerda
        spacer = QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum)
        manage_layout.addItem(spacer)

        # TableWidget para Listar Downloads
        self.download_table = QTableWidget()
        self.download_table.setColumnCount(5)
        self.download_table.setHorizontalHeaderLabels(["ID", "Driver", "Progresso", "Status", "Ações"])
        self.download_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.download_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.download_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.download_table.setAccessibleName("Tabela de Downloads")
        self.download_table.setAccessibleDescription("Lista de downloads ativos com seus status e controles")
        main_layout.addWidget(self.download_table)

        # Aplicar estilos modernos
        self.apply_styles()

    def apply_styles(self):
        # Tema Claro como padrão
        light_stylesheet = """
        QMainWindow {
            background-color: #F0F0F0;
            color: #000000;
        }
        QLabel {
            color: #2E8B57;
        }
        QPushButton {
            background-color: #FFFFFF;
            color: #000000;
            border: 1px solid #CCCCCC;
            border-radius: 5px;
            padding: 5px;
            min-width: 80px;
        }
        QPushButton:hover {
            background-color: #DDDDDD;
        }
        QLineEdit {
            background-color: #FFFFFF;
            color: #000000;
            border: 1px solid #CCCCCC;
            border-radius: 5px;
            padding: 5px;
        }
        QComboBox {
            background-color: #FFFFFF;
            color: #000000;
            border: 1px solid #CCCCCC;
            border-radius: 5px;
            padding: 5px;
        }
        QTableWidget {
            background-color: #FFFFFF;
            color: #000000;
            border: 1px solid #CCCCCC;
        }
        QHeaderView::section {
            background-color: #DDDDDD;
            color: #000000;
            padding: 4px;
            border: 1px solid #CCCCCC;
        }
        QProgressBar {
            border: 1px solid #CCCCCC;
            border-radius: 5px;
            text-align: center;
            height: 20px;
        }
        QProgressBar::chunk {
            background-color: #2E8B57;
            width: 20px;
        }
        """
        self.setStyleSheet(light_stylesheet)

    def adicionar_driver(self):
        nome, ok = QInputDialog.getText(self, "Adicionar Driver", "Nome da Impressora:")
        if not ok or not nome.strip():
            return

        url, ok = QInputDialog.getText(self, "Adicionar Driver", "URL de Download:")
        if not ok or not url.strip():
            return

        grupo, ok = QInputDialog.getItem(
            self, "Adicionar Driver", "Grupo:",
            ["Não Fiscal", "A4"], editable=False
        )
        if not ok:
            return

        checksum, ok = QInputDialog.getText(self, "Adicionar Driver", "Checksum (Opcional):")
        if not ok:
            checksum = ""

        novo_driver = {
            "nome": nome.strip(),
            "url": url.strip(),
            "grupo": grupo
        }
        if checksum.strip():
            novo_driver["checksum"] = checksum.strip()

        self.drivers.append(novo_driver)
        salvar_drivers(self.drivers)
        self.atualizar_table()
        QMessageBox.information(self, "Sucesso", f"Driver '{nome}' adicionado com sucesso!")

    def remover_driver(self):
        selected_rows = self.download_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "Aviso", "Selecione um download para remover.")
            return

        for index in reversed(selected_rows):
            download_id = int(self.download_table.item(index.row(), 0).text())
            driver_name = self.download_table.item(index.row(), 1).text()
            resposta = QMessageBox.question(
                self, "Confirmar Remoção",
                f"Deseja remover o download do driver '{driver_name}'?",
                QMessageBox.Yes | QMessageBox.No
            )

            if resposta == QMessageBox.Yes:
                # Cancelar download se estiver em andamento
                if download_id in self.current_workers:
                    worker = self.current_workers[download_id]
                    worker.cancel()
                    thread = self.current_threads[download_id]
                    thread.quit()
                    thread.wait()
                    del self.current_workers[download_id]
                    del self.current_threads[download_id]

                self.download_table.removeRow(index.row())
                QMessageBox.information(self, "Sucesso", f"Download do driver '{driver_name}' removido com sucesso!")

    def iniciar_download(self, download_id, driver, row):
        # Abrir diálogo para escolher onde salvar
        save_path, _ = QFileDialog.getSaveFileName(
            self, "Salvar Arquivo",
            os.path.join(DOWNLOADS_DIR, os.path.basename(driver['url'])),
            "Executáveis (*.exe);;ZIP (*.zip);;Todos os Arquivos (*)"
        )

        if not save_path:
            return  # Usuário cancelou

        # Definir prioridade
        priority = self.priority_combo.currentIndex() + 1  # 1: Baixa, 2: Média, 3: Alta

        # Iniciar download em uma thread separada usando QThread
        progress_bar = QProgressBar()
        progress_bar.setValue(0)
        self.download_table.setCellWidget(row, 2, progress_bar)

        status_item = QTableWidgetItem("Baixando")
        status_item.setTextAlignment(Qt.AlignCenter)
        self.download_table.setItem(row, 3, status_item)

        # Ações: Botões para pausar e cancelar
        pause_button = QPushButton("Pausar")
        pause_button.setIcon(QIcon.fromTheme("media-playback-pause"))
        cancel_button = QPushButton("Cancelar")
        cancel_button.setIcon(QIcon.fromTheme("process-stop"))

        action_layout = QHBoxLayout()
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.addWidget(pause_button)
        action_layout.addWidget(cancel_button)
        action_widget = QWidget()
        action_widget.setLayout(action_layout)
        self.download_table.setCellWidget(row, 4, action_widget)

        # Conectar botões
        pause_button.clicked.connect(lambda: self.pausar_download(download_id, pause_button))
        cancel_button.clicked.connect(lambda: self.cancelar_download(download_id))

        # Iniciar download
        thread = QThread()
        worker = DownloadWorker(download_id, driver, save_path, priority)
        worker.moveToThread(thread)

        # Conectar sinais e slots
        thread.started.connect(worker.run)
        worker.progress_changed.connect(lambda id, progress: self.update_progress(id, progress))
        worker.download_finished.connect(lambda id, success, message: self.download_finished(id, success, message))
        worker.download_finished.connect(thread.quit)
        worker.download_finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        # Manter referência às threads e workers
        self.current_threads[download_id] = thread
        self.current_workers[download_id] = worker

        thread.start()
        QMessageBox.information(self, "Download Iniciado", f"Iniciando download do driver '{driver['nome']}'.")

    def pausar_download(self, download_id, button):
        if download_id in self.current_workers:
            worker = self.current_workers[download_id]
            if not worker._is_paused:
                worker.pause()
                button.setText("Retomar")
                button.setIcon(QIcon.fromTheme("media-playback-start"))
                self.download_table.setItem(self.get_row_by_id(download_id), 3, QTableWidgetItem("Pausado"))
            else:
                worker.resume()
                button.setText("Pausar")
                button.setIcon(QIcon.fromTheme("media-playback-pause"))
                self.download_table.setItem(self.get_row_by_id(download_id), 3, QTableWidgetItem("Baixando"))

    def cancelar_download(self, download_id):
        if download_id in self.current_workers:
            worker = self.current_workers[download_id]
            worker.cancel()
            self.download_table.setItem(self.get_row_by_id(download_id), 3, QTableWidgetItem("Cancelado"))
            # Após cancelamento, restaurar o botão "Baixar"
            self.restaurar_baixar(download_id)

    def restaurar_baixar(self, download_id):
        row = self.get_row_by_id(download_id)
        if row is not None:
            driver = self.get_driver_by_id(download_id)
            if driver:
                # Remover os botões de pausa e cancelar
                self.download_table.setCellWidget(row, 4, QWidget())

                # Adicionar o botão "Baixar" novamente
                baixar_button = QPushButton("Baixar")
                baixar_button.setIcon(QIcon.fromTheme("download"))
                baixar_button.clicked.connect(lambda _, id=download_id, drv=driver, r=row: self.iniciar_download(id, drv, r))
                baixar_button.setAccessibleName("Botão Baixar")
                baixar_button.setAccessibleDescription("Clique para iniciar o download deste driver")
                self.download_table.setCellWidget(row, 4, baixar_button)

                # Resetar a barra de progresso
                progress_bar = QProgressBar()
                progress_bar.setValue(0)
                self.download_table.setCellWidget(row, 2, progress_bar)

    def update_progress(self, download_id, progress):
        row = self.get_row_by_id(download_id)
        if row is not None:
            progress_bar = self.download_table.cellWidget(row, 2)
            progress_bar.setValue(progress)

    def download_finished(self, download_id, success, message):
        row = self.get_row_by_id(download_id)
        if row is not None:
            if not success:
                if "cancelado" in message.lower():
                    status_text = "Cancelado"
                    # Restaurar o botão "Baixar"
                    self.restaurar_baixar(download_id)
                else:
                    status_text = "Erro"
                    # Remover os botões apenas se for um erro
                    self.download_table.setCellWidget(row, 4, QWidget())
            else:
                status_text = "Concluído"
                # Remover os botões após conclusão bem-sucedida
                self.download_table.setCellWidget(row, 4, QWidget())

            status_item = self.download_table.item(row, 3)
            status_item.setText(status_text)

            QMessageBox.information(self, "Download Finalizado", message)

            # Limpar referências
            if download_id in self.current_workers:
                del self.current_workers[download_id]
            if download_id in self.current_threads:
                del self.current_threads[download_id]

    def restaurar_baixar(self, download_id):
        row = self.get_row_by_id(download_id)
        if row is not None:
            driver = self.get_driver_by_id(download_id)
            if driver:
                # Remover os botões de pausa e cancelar
                self.download_table.setCellWidget(row, 4, QWidget())

                # Adicionar o botão "Baixar" novamente
                baixar_button = QPushButton("Baixar")
                baixar_button.setIcon(QIcon.fromTheme("download"))
                baixar_button.clicked.connect(lambda _, id=download_id, drv=driver, r=row: self.iniciar_download(id, drv, r))
                baixar_button.setAccessibleName("Botão Baixar")
                baixar_button.setAccessibleDescription("Clique para iniciar o download deste driver")
                self.download_table.setCellWidget(row, 4, baixar_button)

                # Resetar a barra de progresso
                progress_bar = QProgressBar()
                progress_bar.setValue(0)
                self.download_table.setCellWidget(row, 2, progress_bar)

    def get_driver_by_name(self, nome):
        for driver in self.drivers:
            if driver['nome'] == nome:
                return driver
        return None

    def get_row_by_id(self, download_id):
        for row in range(self.download_table.rowCount()):
            item = self.download_table.item(row, 0)
            if item and int(item.text()) == download_id:
                return row
        return None

    def show_credits(self):
        credits_text = """
        <h2>Driver Downloader</h2>
        <p>Desenvolvido por <b>Qualifaz Sistemas</b>.</p>
        <p>© 2024 Qualifaz Sistemas. Todos os direitos reservados.</p>
        """
        QMessageBox.information(self, "Créditos", credits_text)

    def filtrar_drivers(self, texto):
        texto = texto.lower()
        for row in range(self.download_table.rowCount()):
            driver_name = self.download_table.item(row, 1).text().lower()
            download_id = int(self.download_table.item(row, 0).text())
            driver = self.get_driver_by_id(download_id)
            grupo = driver.get('grupo', '').lower() if driver else ''
            if texto in driver_name or texto in grupo:
                self.download_table.setRowHidden(row, False)
            else:
                self.download_table.setRowHidden(row, True)

    def get_driver_by_id(self, download_id):
        # Map download_id to driver index (download_id starts at 1)
        index = download_id - 1
        if 0 <= index < len(self.drivers):
            return self.drivers[index]
        return None

    def toggle_theme(self, checked):
        if checked:
            # Tema Escuro
            dark_stylesheet = """
            QMainWindow {
                background-color: #1E1E1E;
                color: #FFFFFF;
            }
            QLabel {
                color: #2E8B57;
            }
            QPushButton {
                background-color: #333333;
                color: #FFFFFF;
                border: 1px solid #555555;
                border-radius: 5px;
                padding: 5px;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #555555;
            }
            QLineEdit {
                background-color: #2E2E2E;
                color: #FFFFFF;
                border: 1px solid #555555;
                border-radius: 5px;
                padding: 5px;
            }
            QComboBox {
                background-color: #2E2E2E;
                color: #FFFFFF;
                border: 1px solid #555555;
                border-radius: 5px;
                padding: 5px;
            }
            QTableWidget {
                background-color: #2E2E2E;
                color: #FFFFFF;
                border: 1px solid #555555;
            }
            QHeaderView::section {
                background-color: #444444;
                color: #FFFFFF;
                padding: 4px;
                border: 1px solid #555555;
            }
            QProgressBar {
                border: 1px solid #555555;
                border-radius: 5px;
                text-align: center;
                height: 20px;
            }
            QProgressBar::chunk {
                background-color: #2E8B57;
                width: 20px;
            }
            """
            self.setStyleSheet(dark_stylesheet)
        else:
            # Tema Claro
            light_stylesheet = """
            QMainWindow {
                background-color: #F0F0F0;
                color: #000000;
            }
            QLabel {
                color: #2E8B57;
            }
            QPushButton {
                background-color: #FFFFFF;
                color: #000000;
                border: 1px solid #CCCCCC;
                border-radius: 5px;
                padding: 5px;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #DDDDDD;
            }
            QLineEdit {
                background-color: #FFFFFF;
                color: #000000;
                border: 1px solid #CCCCCC;
                border-radius: 5px;
                padding: 5px;
            }
            QComboBox {
                background-color: #FFFFFF;
                color: #000000;
                border: 1px solid #CCCCCC;
                border-radius: 5px;
                padding: 5px;
            }
            QTableWidget {
                background-color: #FFFFFF;
                color: #000000;
                border: 1px solid #CCCCCC;
            }
            QHeaderView::section {
                background-color: #DDDDDD;
                color: #000000;
                padding: 4px;
                border: 1px solid #CCCCCC;
            }
            QProgressBar {
                border: 1px solid #CCCCCC;
                border-radius: 5px;
                text-align: center;
                height: 20px;
            }
            QProgressBar::chunk {
                background-color: #2E8B57;
                width: 20px;
            }
            """
            self.setStyleSheet(light_stylesheet)

    # Implementação de Drag and Drop
    def dragEnterEvent(self, event: QtGui.QDragEnterEvent):
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event: QtGui.QDropEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                file_path = url.toLocalFile()
                if os.path.isfile(file_path):
                    self.process_file(file_path)
        elif event.mimeData().hasText():
            text = event.mimeData().text()
            # Supondo que o texto seja uma URL
            self.process_url(text)

    def process_file(self, file_path):
        # Extrair informações do arquivo para adicionar como driver
        nome = os.path.basename(file_path)
        url = f"file:///{os.path.abspath(file_path)}"
        grupo, ok = QInputDialog.getItem(
            self, "Adicionar Driver via Arquivo", "Grupo:",
            ["Não Fiscal", "A4"], editable=False
        )
        if not ok:
            return

        checksum = ""  # Opcional: calcular checksum do arquivo
        novo_driver = {
            "nome": nome,
            "url": url,
            "grupo": grupo,
            "checksum": checksum
        }

        self.drivers.append(novo_driver)
        salvar_drivers(self.drivers)
        self.atualizar_table()
        QMessageBox.information(self, "Sucesso", f"Driver '{nome}' adicionado com sucesso via arquivo!")

    def process_url(self, url):
        nome, ok = QInputDialog.getText(self, "Adicionar Driver via URL", "Nome da Impressora:")
        if not ok or not nome.strip():
            return

        grupo, ok = QInputDialog.getItem(
            self, "Adicionar Driver via URL", "Grupo:",
            ["Não Fiscal", "A4"], editable=False
        )
        if not ok:
            return

        checksum, ok = QInputDialog.getText(self, "Adicionar Driver via URL", "Checksum (Opcional):")
        if not ok:
            checksum = ""

        novo_driver = {
            "nome": nome.strip(),
            "url": url.strip(),
            "grupo": grupo
        }
        if checksum.strip():
            novo_driver["checksum"] = checksum.strip()

        self.drivers.append(novo_driver)
        salvar_drivers(self.drivers)
        self.atualizar_table()
        QMessageBox.information(self, "Sucesso", f"Driver '{nome}' adicionado com sucesso via URL!")

    def exportar_drivers(self):
        export_path, _ = QFileDialog.getSaveFileName(
            self, "Exportar Drivers",
            "drivers_export.json",
            "JSON Files (*.json)"
        )
        if export_path:
            try:
                with open(export_path, 'w', encoding='utf-8') as f:
                    json.dump(self.drivers, f, indent=4, ensure_ascii=False)
                QMessageBox.information(self, "Sucesso", "Drivers exportados com sucesso!")
            except Exception as e:
                QMessageBox.critical(self, "Erro", f"Erro ao exportar drivers: {e}")

    def importar_drivers(self):
        import_path, _ = QFileDialog.getOpenFileName(
            self, "Importar Drivers",
            "",
            "JSON Files (*.json)"
        )
        if import_path:
            try:
                with open(import_path, 'r', encoding='utf-8') as f:
                    imported_drivers = json.load(f)
                # Validação básica
                for driver in imported_drivers:
                    if 'nome' not in driver or 'url' not in driver or 'grupo' not in driver:
                        raise ValueError("Formato de driver inválido.")
                self.drivers.extend(imported_drivers)
                salvar_drivers(self.drivers)
                self.atualizar_table()
                QMessageBox.information(self, "Sucesso", "Drivers importados com sucesso!")
            except Exception as e:
                QMessageBox.critical(self, "Erro", f"Erro ao importar drivers: {e}")

    def atualizar_table(self):
        self.download_table.setRowCount(0)
        self.download_id_counter = 0  # Resetar contador para IDs corretos
        for driver in self.drivers:
            self.add_driver_to_table(driver)

    def add_driver_to_table(self, driver):
        row_position = self.download_table.rowCount()
        self.download_table.insertRow(row_position)

        # ID
        self.download_id_counter += 1
        id_item = QTableWidgetItem(str(self.download_id_counter))
        id_item.setTextAlignment(Qt.AlignCenter)
        self.download_table.setItem(row_position, 0, id_item)

        # Driver
        driver_item = QTableWidgetItem(driver['nome'])
        driver_item.setTextAlignment(Qt.AlignCenter)
        self.download_table.setItem(row_position, 1, driver_item)

        # Progresso
        progress_bar = QProgressBar()
        progress_bar.setValue(0)
        self.download_table.setCellWidget(row_position, 2, progress_bar)

        # Status
        status_item = QTableWidgetItem("Aguardando")
        status_item.setTextAlignment(Qt.AlignCenter)
        self.download_table.setItem(row_position, 3, status_item)

        # Ações
        baixar_button = QPushButton("Baixar")
        baixar_button.setIcon(QIcon.fromTheme("download"))
        baixar_button.clicked.connect(lambda _, id=self.download_id_counter, drv=driver, r=row_position: self.iniciar_download(id, drv, r))
        baixar_button.setAccessibleName("Botão Baixar")
        baixar_button.setAccessibleDescription("Clique para iniciar o download deste driver")
        self.download_table.setCellWidget(row_position, 4, baixar_button)

    def iniciar_download(self, download_id, driver, row):
        # Abrir diálogo para escolher onde salvar
        save_path, _ = QFileDialog.getSaveFileName(
            self, "Salvar Arquivo",
            os.path.join(DOWNLOADS_DIR, os.path.basename(driver['url'])),
            "Executáveis (*.exe);;ZIP (*.zip);;Todos os Arquivos (*)"
        )

        if not save_path:
            return  # Usuário cancelou

        # Definir prioridade
        priority = self.priority_combo.currentIndex() + 1  # 1: Baixa, 2: Média, 3: Alta

        # Iniciar download em uma thread separada usando QThread
        progress_bar = QProgressBar()
        progress_bar.setValue(0)
        self.download_table.setCellWidget(row, 2, progress_bar)

        status_item = QTableWidgetItem("Baixando")
        status_item.setTextAlignment(Qt.AlignCenter)
        self.download_table.setItem(row, 3, status_item)

        # Ações: Botões para pausar e cancelar
        pause_button = QPushButton("Pausar")
        pause_button.setIcon(QIcon.fromTheme("media-playback-pause"))
        cancel_button = QPushButton("Cancelar")
        cancel_button.setIcon(QIcon.fromTheme("process-stop"))

        action_layout = QHBoxLayout()
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.addWidget(pause_button)
        action_layout.addWidget(cancel_button)
        action_widget = QWidget()
        action_widget.setLayout(action_layout)
        self.download_table.setCellWidget(row, 4, action_widget)

        # Conectar botões
        pause_button.clicked.connect(lambda: self.pausar_download(download_id, pause_button))
        cancel_button.clicked.connect(lambda: self.cancelar_download(download_id))

        # Iniciar download
        thread = QThread()
        worker = DownloadWorker(download_id, driver, save_path, priority)
        worker.moveToThread(thread)

        # Conectar sinais e slots
        thread.started.connect(worker.run)
        worker.progress_changed.connect(lambda id, progress: self.update_progress(id, progress))
        worker.download_finished.connect(lambda id, success, message: self.download_finished(id, success, message))
        worker.download_finished.connect(thread.quit)
        worker.download_finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        # Manter referência às threads e workers
        self.current_threads[download_id] = thread
        self.current_workers[download_id] = worker

        thread.start()
        QMessageBox.information(self, "Download Iniciado", f"Iniciando download do driver '{driver['nome']}'.")

    def pausar_download(self, download_id, button):
        if download_id in self.current_workers:
            worker = self.current_workers[download_id]
            if not worker._is_paused:
                worker.pause()
                button.setText("Retomar")
                button.setIcon(QIcon.fromTheme("media-playback-start"))
                self.download_table.setItem(self.get_row_by_id(download_id), 3, QTableWidgetItem("Pausado"))
            else:
                worker.resume()
                button.setText("Pausar")
                button.setIcon(QIcon.fromTheme("media-playback-pause"))
                self.download_table.setItem(self.get_row_by_id(download_id), 3, QTableWidgetItem("Baixando"))

    def cancelar_download(self, download_id):
        if download_id in self.current_workers:
            worker = self.current_workers[download_id]
            worker.cancel()
            self.download_table.setItem(self.get_row_by_id(download_id), 3, QTableWidgetItem("Cancelado"))
            # Após cancelamento, restaurar o botão "Baixar"
            self.restaurar_baixar(download_id)

    def restaurar_baixar(self, download_id):
        row = self.get_row_by_id(download_id)
        if row is not None:
            driver = self.get_driver_by_id(download_id)
            if driver:
                # Remover os botões de pausa e cancelar
                self.download_table.setCellWidget(row, 4, QWidget())

                # Adicionar o botão "Baixar" novamente
                baixar_button = QPushButton("Baixar")
                baixar_button.setIcon(QIcon.fromTheme("download"))
                baixar_button.clicked.connect(lambda _, id=download_id, drv=driver, r=row: self.iniciar_download(id, drv, r))
                baixar_button.setAccessibleName("Botão Baixar")
                baixar_button.setAccessibleDescription("Clique para iniciar o download deste driver")
                self.download_table.setCellWidget(row, 4, baixar_button)

                # Resetar a barra de progresso
                progress_bar = QProgressBar()
                progress_bar.setValue(0)
                self.download_table.setCellWidget(row, 2, progress_bar)

    def update_progress(self, download_id, progress):
        row = self.get_row_by_id(download_id)
        if row is not None:
            progress_bar = self.download_table.cellWidget(row, 2)
            progress_bar.setValue(progress)

    def download_finished(self, download_id, success, message):
        row = self.get_row_by_id(download_id)
        if row is not None:
            if not success:
                if "cancelado" in message.lower():
                    status_text = "Cancelado"
                    # Restaurar o botão "Baixar"
                    self.restaurar_baixar(download_id)
                else:
                    status_text = "Erro"
                    # Remover os botões apenas se for um erro
                    self.download_table.setCellWidget(row, 4, QWidget())
            else:
                status_text = "Concluído"
                # Remover os botões após conclusão bem-sucedida
                self.download_table.setCellWidget(row, 4, QWidget())

            status_item = self.download_table.item(row, 3)
            status_item.setText(status_text)

            QMessageBox.information(self, "Download Finalizado", message)

            # Limpar referências
            if download_id in self.current_workers:
                del self.current_workers[download_id]
            if download_id in self.current_threads:
                del self.current_threads[download_id]

    def get_driver_by_name(self, nome):
        for driver in self.drivers:
            if driver['nome'] == nome:
                return driver
        return None

    def get_row_by_id(self, download_id):
        for row in range(self.download_table.rowCount()):
            item = self.download_table.item(row, 0)
            if item and int(item.text()) == download_id:
                return row
        return None

    def show_credits(self):
        credits_text = """
        <h2>Driver Downloader</h2>
        <p>Desenvolvido por <b>Qualifaz Sistemas</b>.</p>
        <p>© 2024 Qualifaz Sistemas. Todos os direitos reservados.</p>
        """
        QMessageBox.information(self, "Créditos", credits_text)

    def filtrar_drivers(self, texto):
        texto = texto.lower()
        for row in range(self.download_table.rowCount()):
            driver_name = self.download_table.item(row, 1).text().lower()
            download_id = int(self.download_table.item(row, 0).text())
            driver = self.get_driver_by_id(download_id)
            grupo = driver.get('grupo', '').lower() if driver else ''
            if texto in driver_name or texto in grupo:
                self.download_table.setRowHidden(row, False)
            else:
                self.download_table.setRowHidden(row, True)

    def get_driver_by_id(self, download_id):
        # Map download_id to driver index (download_id starts at 1)
        index = download_id - 1
        if 0 <= index < len(self.drivers):
            return self.drivers[index]
        return None

    def toggle_theme(self, checked):
        if checked:
            # Tema Escuro
            dark_stylesheet = """
            QMainWindow {
                background-color: #1E1E1E;
                color: #FFFFFF;
            }
            QLabel {
                color: #2E8B57;
            }
            QPushButton {
                background-color: #333333;
                color: #FFFFFF;
                border: 1px solid #555555;
                border-radius: 5px;
                padding: 5px;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #555555;
            }
            QLineEdit {
                background-color: #2E2E2E;
                color: #FFFFFF;
                border: 1px solid #555555;
                border-radius: 5px;
                padding: 5px;
            }
            QComboBox {
                background-color: #2E2E2E;
                color: #FFFFFF;
                border: 1px solid #555555;
                border-radius: 5px;
                padding: 5px;
            }
            QTableWidget {
                background-color: #2E2E2E;
                color: #FFFFFF;
                border: 1px solid #555555;
            }
            QHeaderView::section {
                background-color: #444444;
                color: #FFFFFF;
                padding: 4px;
                border: 1px solid #555555;
            }
            QProgressBar {
                border: 1px solid #555555;
                border-radius: 5px;
                text-align: center;
                height: 20px;
            }
            QProgressBar::chunk {
                background-color: #2E8B57;
                width: 20px;
            }
            """
            self.setStyleSheet(dark_stylesheet)
        else:
            # Tema Claro
            light_stylesheet = """
            QMainWindow {
                background-color: #F0F0F0;
                color: #000000;
            }
            QLabel {
                color: #2E8B57;
            }
            QPushButton {
                background-color: #FFFFFF;
                color: #000000;
                border: 1px solid #CCCCCC;
                border-radius: 5px;
                padding: 5px;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #DDDDDD;
            }
            QLineEdit {
                background-color: #FFFFFF;
                color: #000000;
                border: 1px solid #CCCCCC;
                border-radius: 5px;
                padding: 5px;
            }
            QComboBox {
                background-color: #FFFFFF;
                color: #000000;
                border: 1px solid #CCCCCC;
                border-radius: 5px;
                padding: 5px;
            }
            QTableWidget {
                background-color: #FFFFFF;
                color: #000000;
                border: 1px solid #CCCCCC;
            }
            QHeaderView::section {
                background-color: #DDDDDD;
                color: #000000;
                padding: 4px;
                border: 1px solid #CCCCCC;
            }
            QProgressBar {
                border: 1px solid #CCCCCC;
                border-radius: 5px;
                text-align: center;
                height: 20px;
            }
            QProgressBar::chunk {
                background-color: #2E8B57;
                width: 20px;
            }
            """
            self.setStyleSheet(light_stylesheet)

    # Implementação de Drag and Drop
    def dragEnterEvent(self, event: QtGui.QDragEnterEvent):
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event: QtGui.QDropEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                file_path = url.toLocalFile()
                if os.path.isfile(file_path):
                    self.process_file(file_path)
        elif event.mimeData().hasText():
            text = event.mimeData().text()
            # Supondo que o texto seja uma URL
            self.process_url(text)

    def process_file(self, file_path):
        # Extrair informações do arquivo para adicionar como driver
        nome = os.path.basename(file_path)
        url = f"file:///{os.path.abspath(file_path)}"
        grupo, ok = QInputDialog.getItem(
            self, "Adicionar Driver via Arquivo", "Grupo:",
            ["Não Fiscal", "A4"], editable=False
        )
        if not ok:
            return

        checksum = ""  # Opcional: calcular checksum do arquivo
        novo_driver = {
            "nome": nome,
            "url": url,
            "grupo": grupo,
            "checksum": checksum
        }

        self.drivers.append(novo_driver)
        salvar_drivers(self.drivers)
        self.atualizar_table()
        QMessageBox.information(self, "Sucesso", f"Driver '{nome}' adicionado com sucesso via arquivo!")

    def process_url(self, url):
        nome, ok = QInputDialog.getText(self, "Adicionar Driver via URL", "Nome da Impressora:")
        if not ok or not nome.strip():
            return

        grupo, ok = QInputDialog.getItem(
            self, "Adicionar Driver via URL", "Grupo:",
            ["Não Fiscal", "A4"], editable=False
        )
        if not ok:
            return

        checksum, ok = QInputDialog.getText(self, "Adicionar Driver via URL", "Checksum (Opcional):")
        if not ok:
            checksum = ""

        novo_driver = {
            "nome": nome.strip(),
            "url": url.strip(),
            "grupo": grupo
        }
        if checksum.strip():
            novo_driver["checksum"] = checksum.strip()

        self.drivers.append(novo_driver)
        salvar_drivers(self.drivers)
        self.atualizar_table()
        QMessageBox.information(self, "Sucesso", f"Driver '{nome}' adicionado com sucesso via URL!")

    def exportar_drivers(self):
        export_path, _ = QFileDialog.getSaveFileName(
            self, "Exportar Drivers",
            "drivers_export.json",
            "JSON Files (*.json)"
        )
        if export_path:
            try:
                with open(export_path, 'w', encoding='utf-8') as f:
                    json.dump(self.drivers, f, indent=4, ensure_ascii=False)
                QMessageBox.information(self, "Sucesso", "Drivers exportados com sucesso!")
            except Exception as e:
                QMessageBox.critical(self, "Erro", f"Erro ao exportar drivers: {e}")

    def importar_drivers(self):
        import_path, _ = QFileDialog.getOpenFileName(
            self, "Importar Drivers",
            "",
            "JSON Files (*.json)"
        )
        if import_path:
            try:
                with open(import_path, 'r', encoding='utf-8') as f:
                    imported_drivers = json.load(f)
                # Validação básica
                for driver in imported_drivers:
                    if 'nome' not in driver or 'url' not in driver or 'grupo' not in driver:
                        raise ValueError("Formato de driver inválido.")
                self.drivers.extend(imported_drivers)
                salvar_drivers(self.drivers)
                self.atualizar_table()
                QMessageBox.information(self, "Sucesso", "Drivers importados com sucesso!")
            except Exception as e:
                QMessageBox.critical(self, "Erro", f"Erro ao importar drivers: {e}")

    def atualizar_table(self):
        self.download_table.setRowCount(0)
        self.download_id_counter = 0  # Resetar contador para IDs corretos
        for driver in self.drivers:
            self.add_driver_to_table(driver)

    def add_driver_to_table(self, driver):
        row_position = self.download_table.rowCount()
        self.download_table.insertRow(row_position)

        # ID
        self.download_id_counter += 1
        id_item = QTableWidgetItem(str(self.download_id_counter))
        id_item.setTextAlignment(Qt.AlignCenter)
        self.download_table.setItem(row_position, 0, id_item)

        # Driver
        driver_item = QTableWidgetItem(driver['nome'])
        driver_item.setTextAlignment(Qt.AlignCenter)
        self.download_table.setItem(row_position, 1, driver_item)

        # Progresso
        progress_bar = QProgressBar()
        progress_bar.setValue(0)
        self.download_table.setCellWidget(row_position, 2, progress_bar)

        # Status
        status_item = QTableWidgetItem("Aguardando")
        status_item.setTextAlignment(Qt.AlignCenter)
        self.download_table.setItem(row_position, 3, status_item)

        # Ações
        baixar_button = QPushButton("Baixar")
        baixar_button.setIcon(QIcon.fromTheme("download"))
        baixar_button.clicked.connect(lambda _, id=self.download_id_counter, drv=driver, r=row_position: self.iniciar_download(id, drv, r))
        baixar_button.setAccessibleName("Botão Baixar")
        baixar_button.setAccessibleDescription("Clique para iniciar o download deste driver")
        self.download_table.setCellWidget(row_position, 4, baixar_button)

    def iniciar_download(self, download_id, driver, row):
        # Abrir diálogo para escolher onde salvar
        save_path, _ = QFileDialog.getSaveFileName(
            self, "Salvar Arquivo",
            os.path.join(DOWNLOADS_DIR, os.path.basename(driver['url'])),
            "Executáveis (*.exe);;ZIP (*.zip);;Todos os Arquivos (*)"
        )

        if not save_path:
            return  # Usuário cancelou

        # Definir prioridade
        priority = self.priority_combo.currentIndex() + 1  # 1: Baixa, 2: Média, 3: Alta

        # Iniciar download em uma thread separada usando QThread
        progress_bar = QProgressBar()
        progress_bar.setValue(0)
        self.download_table.setCellWidget(row, 2, progress_bar)

        status_item = QTableWidgetItem("Baixando")
        status_item.setTextAlignment(Qt.AlignCenter)
        self.download_table.setItem(row, 3, status_item)

        # Ações: Botões para pausar e cancelar
        pause_button = QPushButton("Pausar")
        pause_button.setIcon(QIcon.fromTheme("media-playback-pause"))
        cancel_button = QPushButton("Cancelar")
        cancel_button.setIcon(QIcon.fromTheme("process-stop"))

        action_layout = QHBoxLayout()
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.addWidget(pause_button)
        action_layout.addWidget(cancel_button)
        action_widget = QWidget()
        action_widget.setLayout(action_layout)
        self.download_table.setCellWidget(row, 4, action_widget)

        # Conectar botões
        pause_button.clicked.connect(lambda: self.pausar_download(download_id, pause_button))
        cancel_button.clicked.connect(lambda: self.cancelar_download(download_id))

        # Iniciar download
        thread = QThread()
        worker = DownloadWorker(download_id, driver, save_path, priority)
        worker.moveToThread(thread)

        # Conectar sinais e slots
        thread.started.connect(worker.run)
        worker.progress_changed.connect(lambda id, progress: self.update_progress(id, progress))
        worker.download_finished.connect(lambda id, success, message: self.download_finished(id, success, message))
        worker.download_finished.connect(thread.quit)
        worker.download_finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        # Manter referência às threads e workers
        self.current_threads[download_id] = thread
        self.current_workers[download_id] = worker

        thread.start()
        QMessageBox.information(self, "Download Iniciado", f"Iniciando download do driver '{driver['nome']}'.")

    def pausar_download(self, download_id, button):
        if download_id in self.current_workers:
            worker = self.current_workers[download_id]
            if not worker._is_paused:
                worker.pause()
                button.setText("Retomar")
                button.setIcon(QIcon.fromTheme("media-playback-start"))
                self.download_table.setItem(self.get_row_by_id(download_id), 3, QTableWidgetItem("Pausado"))
            else:
                worker.resume()
                button.setText("Pausar")
                button.setIcon(QIcon.fromTheme("media-playback-pause"))
                self.download_table.setItem(self.get_row_by_id(download_id), 3, QTableWidgetItem("Baixando"))

    def cancelar_download(self, download_id):
        if download_id in self.current_workers:
            worker = self.current_workers[download_id]
            worker.cancel()
            self.download_table.setItem(self.get_row_by_id(download_id), 3, QTableWidgetItem("Cancelado"))
            # Após cancelamento, restaurar o botão "Baixar"
            self.restaurar_baixar(download_id)

    def restaurar_baixar(self, download_id):
        row = self.get_row_by_id(download_id)
        if row is not None:
            driver = self.get_driver_by_id(download_id)
            if driver:
                # Remover os botões de pausa e cancelar
                self.download_table.setCellWidget(row, 4, QWidget())

                # Adicionar o botão "Baixar" novamente
                baixar_button = QPushButton("Baixar")
                baixar_button.setIcon(QIcon.fromTheme("download"))
                baixar_button.clicked.connect(lambda _, id=download_id, drv=driver, r=row: self.iniciar_download(id, drv, r))
                baixar_button.setAccessibleName("Botão Baixar")
                baixar_button.setAccessibleDescription("Clique para iniciar o download deste driver")
                self.download_table.setCellWidget(row, 4, baixar_button)

                # Resetar a barra de progresso
                progress_bar = QProgressBar()
                progress_bar.setValue(0)
                self.download_table.setCellWidget(row, 2, progress_bar)

    def update_progress(self, download_id, progress):
        row = self.get_row_by_id(download_id)
        if row is not None:
            progress_bar = self.download_table.cellWidget(row, 2)
            progress_bar.setValue(progress)

    def download_finished(self, download_id, success, message):
        row = self.get_row_by_id(download_id)
        if row is not None:
            if not success:
                if "cancelado" in message.lower():
                    status_text = "Cancelado"
                    # Restaurar o botão "Baixar"
                    self.restaurar_baixar(download_id)
                else:
                    status_text = "Erro"
                    # Remover os botões apenas se for um erro
                    self.download_table.setCellWidget(row, 4, QWidget())
            else:
                status_text = "Concluído"
                # Remover os botões após conclusão bem-sucedida
                self.download_table.setCellWidget(row, 4, QWidget())

            status_item = self.download_table.item(row, 3)
            status_item.setText(status_text)

            QMessageBox.information(self, "Download Finalizado", message)

            # Limpar referências
            if download_id in self.current_workers:
                del self.current_workers[download_id]
            if download_id in self.current_threads:
                del self.current_threads[download_id]

    def get_driver_by_name(self, nome):
        for driver in self.drivers:
            if driver['nome'] == nome:
                return driver
        return None

    def get_row_by_id(self, download_id):
        for row in range(self.download_table.rowCount()):
            item = self.download_table.item(row, 0)
            if item and int(item.text()) == download_id:
                return row
        return None

    def show_credits(self):
        credits_text = """
        <h2>Driver Downloader</h2>
        <p>Desenvolvido por <b>Qualifaz Sistemas</b>.</p>
        <p>© 2024 Qualifaz Sistemas. Todos os direitos reservados.</p>
        """
        QMessageBox.information(self, "Créditos", credits_text)

    def filtrar_drivers(self, texto):
        texto = texto.lower()
        for row in range(self.download_table.rowCount()):
            driver_name = self.download_table.item(row, 1).text().lower()
            download_id = int(self.download_table.item(row, 0).text())
            driver = self.get_driver_by_id(download_id)
            grupo = driver.get('grupo', '').lower() if driver else ''
            if texto in driver_name or texto in grupo:
                self.download_table.setRowHidden(row, False)
            else:
                self.download_table.setRowHidden(row, True)

    def get_driver_by_id(self, download_id):
        # Map download_id to driver index (download_id starts at 1)
        index = download_id - 1
        if 0 <= index < len(self.drivers):
            return self.drivers[index]
        return None

    def toggle_theme(self, checked):
        if checked:
            # Tema Escuro
            dark_stylesheet = """
            QMainWindow {
                background-color: #1E1E1E;
                color: #FFFFFF;
            }
            QLabel {
                color: #2E8B57;
            }
            QPushButton {
                background-color: #333333;
                color: #FFFFFF;
                border: 1px solid #555555;
                border-radius: 5px;
                padding: 5px;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #555555;
            }
            QLineEdit {
                background-color: #2E2E2E;
                color: #FFFFFF;
                border: 1px solid #555555;
                border-radius: 5px;
                padding: 5px;
            }
            QComboBox {
                background-color: #2E2E2E;
                color: #FFFFFF;
                border: 1px solid #555555;
                border-radius: 5px;
                padding: 5px;
            }
            QTableWidget {
                background-color: #2E2E2E;
                color: #FFFFFF;
                border: 1px solid #555555;
            }
            QHeaderView::section {
                background-color: #444444;
                color: #FFFFFF;
                padding: 4px;
                border: 1px solid #555555;
            }
            QProgressBar {
                border: 1px solid #555555;
                border-radius: 5px;
                text-align: center;
                height: 20px;
            }
            QProgressBar::chunk {
                background-color: #2E8B57;
                width: 20px;
            }
            """
            self.setStyleSheet(dark_stylesheet)
        else:
            # Tema Claro
            light_stylesheet = """
            QMainWindow {
                background-color: #F0F0F0;
                color: #000000;
            }
            QLabel {
                color: #2E8B57;
            }
            QPushButton {
                background-color: #FFFFFF;
                color: #000000;
                border: 1px solid #CCCCCC;
                border-radius: 5px;
                padding: 5px;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #DDDDDD;
            }
            QLineEdit {
                background-color: #FFFFFF;
                color: #000000;
                border: 1px solid #CCCCCC;
                border-radius: 5px;
                padding: 5px;
            }
            QComboBox {
                background-color: #FFFFFF;
                color: #000000;
                border: 1px solid #CCCCCC;
                border-radius: 5px;
                padding: 5px;
            }
            QTableWidget {
                background-color: #FFFFFF;
                color: #000000;
                border: 1px solid #CCCCCC;
            }
            QHeaderView::section {
                background-color: #DDDDDD;
                color: #000000;
                padding: 4px;
                border: 1px solid #CCCCCC;
            }
            QProgressBar {
                border: 1px solid #CCCCCC;
                border-radius: 5px;
                text-align: center;
                height: 20px;
            }
            QProgressBar::chunk {
                background-color: #2E8B57;
                width: 20px;
            }
            """
            self.setStyleSheet(light_stylesheet)

# Executar a aplicação
def main():
    app = QApplication(sys.argv)

    # Definir ícones do tema (opcional, dependendo do SO)
    if hasattr(QStyle, 'setStyle'):
        app.setStyle('Fusion')

    window = DriverDownloaderApp()
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
