import os
from PyQt5.QtWidgets import *
from PyQt5.QtGui import *
from PyQt5.Qt import QDesktopServices, QUrl
from PyQt5.QtCore import *
from typing import Optional

DEBUGGING = None
if os.environ.get("VOCABSIEVE_DEBUG"):
    DEBUGGING = True
    QCoreApplication.setApplicationName(
        "VocabSieve" + os.environ.get("VOCABSIEVE_DEBUG", ""))
else:
    QCoreApplication.setApplicationName("VocabSieve")
QCoreApplication.setOrganizationName("FreeLanguageTools")

from .config import *
from .tools import *
from .db import *
from .dictionary import *
from .api import LanguageServer
from . import __version__
from .ext.reader import ReaderServer
from .ext.importer import KindleImporter, KoreaderImporter
import sys
import importlib
import functools
import requests
import platform
import time
import json
import csv
from packaging import version
from markdown import markdown
from markdownify import markdownify
from datetime import datetime
import re
Path(os.path.join(datapath, "images")).mkdir(parents=True, exist_ok=True)
# If on macOS, display the modifier key as "Cmd", else display it as "Ctrl".
# For whatever reason, Qt automatically uses Cmd key when Ctrl is specified on Mac
# so there is no need to change the keybind, only the display text
if platform.system() == "Darwin":
    MOD = "Cmd"
else:
    MOD = "Ctrl"


@functools.lru_cache()
class GlobalObject(QObject):
    """
    We need this to enable the textedit widget to communicate with the main window
    """

    def __init__(self):
        super().__init__()
        self._events = {}

    def addEventListener(self, name, func):
        if name not in self._events:
            self._events[name] = [func]
        else:
            self._events[name].append(func)

    def dispatchEvent(self, name):
        functions = self._events.get(name, [])
        for func in functions:
            QTimer.singleShot(0, func)


class MyTextEdit(QTextEdit):

    @pyqtSlot()
    def mouseDoubleClickEvent(self, e):
        super().mouseDoubleClickEvent(e)
        GlobalObject().dispatchEvent("double clicked")
        self.textCursor().clearSelection()
        self.original = ""


class DictionaryWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VocabSieve" + os.environ.get("VOCABSIEVE_DEBUG", ""))
        self.setFocusPolicy(Qt.StrongFocus)
        self.widget = QWidget()
        self.settings = QSettings()
        self.rec = Record()
        self.setCentralWidget(self.widget)
        self.previousWord = ""
        self.audio_path = ""
        self.image_path = None 
        self.scaleFont()
        self.initWidgets()
        if self.settings.value("orientation", "Vertical") == "Vertical":
            self.resize(400, 700)
            self.setupWidgetsV()
        else:
            self.resize(1000, 300)
            self.setupWidgetsH()
        self.setupMenu()
        self.setupButtons()
        self.startServer()
        self.initTimer()
        self.updateAnkiButtonState()
        self.setupShortcuts()
        self.checkUpdates()

        GlobalObject().addEventListener("double clicked", self.lookupClicked)
        if self.settings.value("primary", False, type=bool)\
                and QClipboard.supportsSelection(QApplication.clipboard()):
            QApplication.clipboard().selectionChanged.connect(
                lambda: self.clipboardChanged(True))
        QApplication.clipboard().dataChanged.connect(self.clipboardChanged)

    def scaleFont(self):
        font = QApplication.font()
        font.setPointSize(
            int(font.pointSize() * self.settings.value("text_scale", type=int) / 100))
        self.setFont(font)

    def focusInEvent(self, event):
        if platform.system() == "Darwin":
            self.clipboardChanged()
        super().focusInEvent(event)

    def checkUpdates(self):
        if self.settings.value("check_updates") is None:
            answer = QMessageBox.question(
                self,
                "Check updates",
                "<h2>Would you like VocabSieve to check for updates automatically?</h2>"
                "Currently, the repository and releases are hosted on GitHub's servers, "
                "which will be queried for checking updates. <br>VocabSieve cannot and "
                "<strong>will not</strong> install any updates automatically."
                "<br>You can change this option in the configuration panel at a later date."
            )
            if answer == QMessageBox.Yes:
                self.settings.setValue("check_updates", True)
            if answer == QMessageBox.No:
                self.settings.setValue("check_updates", False)
        elif self.settings.value("check_updates", True, type=bool):
            try:
                res = requests.get("https://api.github.com/repos/FreeLanguageTools/vocabsieve/releases")
                data = res.json()
            except Exception:
                return
            latest_version = (current := data[0])['tag_name'].strip('v')
            current_version = importlib.metadata.version('vocabsieve')
            print(current_version, latest_version)
            if version.parse(latest_version) > version.parse(current_version):
                answer2 = QMessageBox.information(
                    self,
                    "New version",
                    "<h2>There is a new version available!</h2>"
                    + f"<h3>Version {latest_version}</h3>"
                    + markdown(current['body']),
                    buttons=QMessageBox.Open | QMessageBox.Ignore
                )
                if answer2 == QMessageBox.Open:
                    QDesktopServices.openUrl(QUrl(current['html_url']))
        else:
            pass

    def initWidgets(self):
        if os.environ.get("VOCABSIEVE_DEBUG"):
            self.namelabel = QLabel(
                "<h2 style=\"font-weight: normal;\">VocabSieve"
                " (debug=" + os.environ.get("VOCABSIEVE_DEBUG", "")
                + ")</h2>")
        else:
            self.namelabel = QLabel(
                "<h2 style=\"font-weight: normal;\">VocabSieve v" +
                __version__ +
                "</h2>")
        self.menu = QMenuBar(self)
        self.sentence = MyTextEdit()
        self.sentence.setPlaceholderText(
            "Sentence copied to the clipboard will show up here.")
        self.sentence.setMinimumHeight(50)
        #self.sentence.setMaximumHeight(300)
        self.word = QLineEdit()
        self.word.setPlaceholderText("Word will appear here when looked up.")
        self.definition = MyTextEdit()
        self.definition.setMinimumHeight(70)
        #self.definition.setMaximumHeight(1800)
        self.definition2 = MyTextEdit()
        self.definition2.setMinimumHeight(70)
        #self.definition2.setMaximumHeight(1800)
        self.tags = QLineEdit()
        self.tags.setPlaceholderText(
            "Type in a list of tags to be used, separated by spaces (same as in Anki).")
        self.sentence.setToolTip(
            "You can look up any word in this box by double clicking it, or alternatively by selecting it"
            ", then press \"Get definition\".")

        self.lookup_button = QPushButton(f"Define [{MOD}-D]")
        self.lookup_exact_button = QPushButton(f"Define Direct [Shift-{MOD}-D]")
        self.lookup_exact_button.setToolTip(
            "This will look up the word without lemmatization.")
        self.toanki_button = QPushButton(f"Add note [{MOD}-S]")

        self.config_button = QPushButton("Configure..")
        self.read_button = QPushButton(f"Read clipboard [{MOD}-V]")
        self.bar = QStatusBar()
        self.setStatusBar(self.bar)
        self.stats_label = QLabel()

        self.single_word = QCheckBox("Single word lookups")
        self.single_word.setToolTip(
            "If enabled, vocabsieve will act as a quick dictionary and look up any single words copied to the clipboard.\n"
            "This can potentially send your clipboard contents over the network if an online dictionary service is used.\n"
            "This is INSECURE if you use password managers that copy passwords to the clipboard.")

        self.web_button = QPushButton(f"Open webpage [{MOD}-1]")
        self.freq_display = QLineEdit()
        self.freq_display.setPlaceholderText("Frequency index")

        self.freq_stars_display = QLineEdit()
        self.freq_stars_display.setPlaceholderText("Freq stars")

        self.audio_selector = QListWidget()
        self.audio_selector.setMinimumHeight(50)
        self.audio_selector.setFlow(QListView.TopToBottom)
        self.audio_selector.setResizeMode(QListView.Adjust)
        self.audio_selector.setWrapping(True)

        self.audio_selector.currentItemChanged.connect(lambda x: (
            self.play_audio(x.text()[2:]) if x is not None else None
        ))

        self.definition.setReadOnly(
            not (
                self.settings.value(
                    "allow_editing",
                    True,
                    type=bool)))
        self.definition2.setReadOnly(
            not (
                self.settings.value(
                    "allow_editing",
                    True,
                    type=bool)))
        self.definition.setPlaceholderText(
            'You can look up any word in the "Sentence" box by double clicking it, or alternatively by selecting it, then press "Get definition".')
        self.definition2.setPlaceholderText(
            'You can look up any word in the "Sentence" box by double clicking it, or alternatively by selecting it, then press "Get definition".')

        self.image_viewer = QLabel("<center><b>&lt;No image selected&gt;</center>")
        self.image_viewer.setScaledContents(True)
        self.image_viewer.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.image_viewer.setStyleSheet(
            '''
                border: 1px solid black;
            '''
        )

    def play_audio(self, x):
        QCoreApplication.processEvents()
        if x is not None:
            self.audio_path = play_audio(
                x, self.audios, self.settings.value(
                    "target_language", "en"))

    def setupWidgetsV(self):
        self.layout = QGridLayout(self.widget)
        self.layout.addWidget(self.namelabel, 0, 0, 1, 2)

        self.layout.addWidget(self.single_word, 1, 0, 1, 3)

        self.layout.addWidget(
            QLabel("<h3 style=\"font-weight: normal;\">Sentence</h3>"), 2, 0)
        self.layout.addWidget(self.read_button, 2, 1)
        self.layout.addWidget(self.image_viewer, 0, 2, 3, 1)
        self.layout.addWidget(self.sentence, 3, 0, 1, 3)
        self.layout.setRowStretch(3, 1)
        self.layout.addWidget(
            QLabel("<h3 style=\"font-weight: normal;\">Word</h3>"), 4, 0)

        if self.settings.value("lemmatization", True, type=bool):
            self.layout.addWidget(self.lookup_button, 4, 1)
            self.layout.addWidget(self.lookup_exact_button, 4, 2)
        else:
            self.layout.addWidget(self.lookup_button, 4, 1, 1, 2)

        if self.settings.value("freq_source", "<disabled>") != "<disabled>":
            self.layout.addWidget(QLabel("<h3 style=\"font-weight: normal;\">Frequency</h3>"), 6, 0)
            self.layout.addWidget(self.freq_stars_display, 7, 0)
            self.layout.addWidget(self.freq_display, 7, 1)

        self.layout.addWidget(
            QLabel("<h3 style=\"font-weight: normal;\">Definition</h3>"), 8, 0)
        self.layout.addWidget(self.web_button, 8, 2)
        self.layout.addWidget(self.word, 5, 0, 1, 3)
        self.layout.setRowStretch(9, 2)
        self.layout.setRowStretch(11, 2)
        if self.settings.value("dict_source2", "<disabled>") != "<disabled>":
            self.layout.addWidget(self.definition, 9, 0, 2, 3)
            self.layout.addWidget(self.definition2, 11, 0, 2, 3)
        else:
            self.layout.addWidget(self.definition, 9, 0, 4, 3)

        self.layout.addWidget(
            QLabel("<h3 style=\"font-weight: normal;\">Pronunciation</h3>"),
            13,
            0,
            1,
            3)
        self.layout.addWidget(self.audio_selector, 14, 0, 1, 3)
        self.layout.setRowStretch(14, 1)
        self.layout.addWidget(
            QLabel("<h3 style=\"font-weight: normal;\">Additional tags</h3>"),
            15,
            0,
            1,
            3)

        self.layout.addWidget(self.tags, 16, 0, 1, 3)

        self.layout.addWidget(self.toanki_button, 17, 0, 1, 3)
        self.layout.addWidget(self.config_button, 18, 0, 1, 3)

    def setupButtons(self):
        self.lookup_button.clicked.connect(lambda: self.lookupClicked(True))
        self.lookup_exact_button.clicked.connect(
            lambda: self.lookupClicked(False))

        self.web_button.clicked.connect(self.onWebButton)

        self.config_button.clicked.connect(self.configure)
        self.toanki_button.clicked.connect(self.createNote)
        self.read_button.clicked.connect(lambda: self.clipboardChanged())

        self.sentence.textChanged.connect(self.updateAnkiButtonState)

        self.bar.addPermanentWidget(self.stats_label)

    def setupMenu(self):
        self.open_reader_action = QAction("&Reader")
        self.menu.addAction(self.open_reader_action)
        if not self.settings.value("reader_enabled", True, type=bool):
            self.open_reader_action.setEnabled(False)
        importmenu = self.menu.addMenu("&Import")
        exportmenu = self.menu.addMenu("&Export")
        helpmenu = self.menu.addMenu("&Help")
        self.help_action = QAction("&Help")
        self.about_action = QAction("&About")
        helpmenu.addAction(self.help_action)
        helpmenu.addAction(self.about_action)

        self.import_koreader_action = QAction("Import K&OReader")
        self.import_kindle_action = QAction("Import &Kindle")

        self.export_notes_csv_action = QAction("Export &notes to CSV")
        self.export_lookups_csv_action = QAction("Export &lookup data to CSV")

        self.help_action.triggered.connect(self.onHelp)
        self.about_action.triggered.connect(self.onAbout)
        self.open_reader_action.triggered.connect(self.onReaderOpen)
        self.import_koreader_action.triggered.connect(self.importkoreader)
        self.import_kindle_action.triggered.connect(self.importkindle)
        self.export_notes_csv_action.triggered.connect(self.exportNotes)
        self.export_lookups_csv_action.triggered.connect(self.exportLookups)

        importmenu.addActions(
            [self.import_koreader_action, self.import_kindle_action]
        )

        exportmenu.addActions(
            [self.export_notes_csv_action, self.export_lookups_csv_action]
        )

        self.setMenuBar(self.menu)

    def exportNotes(self):
        """
        First ask for a file path, then save a CSV there.
        """
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save CSV to file",
            os.path.join(
                QStandardPaths.writableLocation(QStandardPaths.DesktopLocation),
                f"vocabsieve-notes-{datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}.csv"
            ),
            "CSV (*.csv)"
        )
        if path:
            with open(path, 'w') as file:
                writer = csv.writer(file)
                writer.writerow(
                    ['timestamp', 'content', 'anki_export_success', 'sentence', 'word', 
                    'definition', 'definition2', 'pronunciation', 'image', 'tags']
                )
                writer.writerows(self.rec.getAllNotes())
        else:
            return

    def exportLookups(self):
        """
        First ask for a file path, then save a CSV there.
        """
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save CSV to file",
            os.path.join(
                QStandardPaths.writableLocation(QStandardPaths.DesktopLocation),
                f"vocabsieve-lookups-{datetime.now().strftime('%Y-%m-%d-%H-%M-%S')}.csv"
            ),
            "CSV (*.csv)"
        )
        if path:
            with open(path, 'w') as file:
                writer = csv.writer(file)
                writer.writerow(
                    ['timestamp', 'word', 'definition', 'language', 'lemmatize', 'dictionary', 'success']
                )
                writer.writerows(self.rec.getAllLookups())
        else:
            return

    def onHelp(self):
        url = f"https://wiki.freelanguagetools.org/vocabsieve_setup"
        QDesktopServices.openUrl(QUrl(url))

    def onAbout(self):
        self.about_dialog = AboutDialog()
        self.about_dialog.exec_()

    def setupWidgetsH(self):
        self.layout = QGridLayout(self.widget)
        # self.sentence.setMaximumHeight(99999)
        self.layout.addWidget(self.namelabel, 0, 0, 1, 1)
        self.layout.addWidget(self.image_viewer, 0, 1, 2, 1)
        self.layout.addWidget(self.single_word, 0, 3, 1, 2)

        self.layout.addWidget(
            QLabel("<h3 style=\"font-weight: normal;\">Sentence</h3>"), 1, 0)
        self.layout.addWidget(self.read_button, 6, 1)

        self.layout.addWidget(self.sentence, 2, 0, 3, 2)
        self.layout.addWidget(self.audio_selector, 5, 0, 1, 2)
        self.layout.addWidget(
            QLabel("<h3 style=\"font-weight: normal;\">Word</h3>"), 0, 2)

        self.layout.addWidget(self.lookup_button, 3, 2)
        self.layout.addWidget(self.lookup_exact_button, 4, 2)
        self.layout.addWidget(self.freq_stars_display, 2, 2)
        #self.layout.addWidget(self.freq_display, 6, 2)

        self.layout.addWidget(
            QLabel("<h3 style=\"font-weight: normal;\">Definition</h3>"), 1, 3)
        self.layout.addWidget(self.web_button, 1, 4)
        self.layout.addWidget(self.word, 1, 2, 1, 1)
        if self.settings.value("dict_source2", "<disabled>") != "<disabled>":
            self.layout.addWidget(self.definition, 2, 3, 4, 1)
            self.layout.addWidget(self.definition2, 2, 4, 4, 1)
        else:
            self.layout.addWidget(self.definition, 2, 3, 4, 2)

        self.layout.addWidget(QLabel("Additional tags"), 5, 2, 1, 1)

        self.layout.addWidget(self.tags, 6, 2)

        self.layout.addWidget(self.toanki_button, 6, 3, 1, 1)
        self.layout.addWidget(self.config_button, 6, 4, 1, 1)
        self.layout.setColumnStretch(0, 2)
        self.layout.setColumnStretch(1, 2)
        self.layout.setColumnStretch(2, 0)
        self.layout.setColumnStretch(3, 5)
        self.layout.setColumnStretch(4, 5)
        self.layout.setRowStretch(0, 0)
        #self.layout.setRowStretch(1, 5)
        self.layout.setRowStretch(2, 5)
        self.layout.setRowStretch(3, 5)
        self.layout.setRowStretch(4, 5)
        self.layout.setRowStretch(5, 5)
        self.layout.setRowStretch(6, 0)

    def updateAnkiButtonState(self, forceDisable=False):
        if self.sentence.toPlainText() == "" or forceDisable:
            self.toanki_button.setEnabled(False)
        else:
            self.toanki_button.setEnabled(True)

    def configure(self):
        api = self.settings.value('anki_api', 'http://127.0.0.1:8765')
        if self.settings.value('enable_anki', True, type=bool):
            try:
                _ = getVersion(api)
            except Exception as e:
                print(e)
                answer = QMessageBox.question(
                    self,
                    "Could not reach AnkiConnect",
                    "<h2>Could not reach AnkiConnect</h2>"
                    "AnkiConnect is required for changing Anki-related settings."
                    "<br>Choose 'Ignore' to not change Anki settings this time"
                    "<br>Choose 'Abort' to not open the configuration window"
                    "<br><br>If you have AnkiConnect listening to a non-default port or address, "
                    "select 'Ignore and change the Anki API option on the Anki tab, and "
                    "reopen the configuration window."
                    "<br><br>If you do not wish to use Anki with this program, select 'Ignore' "
                    "and then uncheck the 'Enable Anki' checkbox on the Anki tab.",
                    buttons=QMessageBox.Ignore | QMessageBox.Abort,
                    defaultButton=QMessageBox.Ignore
                )
                if answer == QMessageBox.Ignore:
                    pass
                if answer == QMessageBox.Abort:
                    return
        self.settings_dialog = SettingsDialog(self)
        self.settings_dialog.exec()

    def importkindle(self):
        fname = QFileDialog.getOpenFileName(
            parent=self,
            caption="Select a file",
            filter='Kindle clippings files (*.txt)',
        )[0]
        if not fname:
            return
        else:
            import_kindle = KindleImporter(self, fname)
            import_kindle.exec()

    def importkoreader(self):
        path = QFileDialog.getExistingDirectory(
            parent=self,
            caption="Select the directory containers ebook files",
            directory=QStandardPaths.writableLocation(QStandardPaths.HomeLocation)
        )
        if not path:
            return
        else:
            import_koreader = KoreaderImporter(self, path)
            import_koreader.exec()

    def setupShortcuts(self):
        self.shortcut_toanki = QShortcut(QKeySequence('Ctrl+S'), self)
        self.shortcut_toanki.activated.connect(self.toanki_button.animateClick)
        self.shortcut_getdef_e = QShortcut(QKeySequence('Ctrl+Shift+D'), self)
        self.shortcut_getdef_e.activated.connect(self.lookup_exact_button.animateClick)
        self.shortcut_getdef = QShortcut(QKeySequence('Ctrl+D'), self)
        self.shortcut_getdef.activated.connect(self.lookup_button.animateClick)
        self.shortcut_paste = QShortcut(QKeySequence('Ctrl+V'), self)
        self.shortcut_paste.activated.connect(self.read_button.animateClick)
        self.shortcut_web = QShortcut(QKeySequence('Ctrl+1'), self)
        self.shortcut_web.activated.connect(self.web_button.animateClick)

    def getCurrentWord(self):
        cursor = self.sentence.textCursor()
        selected = cursor.selectedText()
        cursor2 = self.definition.textCursor()
        selected2 = cursor2.selectedText()
        cursor3 = self.definition2.textCursor()
        selected3 = cursor3.selectedText()
        target = str.strip(selected
                           or selected2
                           or selected3
                           or self.previousWord
                           or self.word.text()
                           or "")
        self.previousWord = target

        return target

    def onWebButton(self):
        url = self.settings.value("custom_url",
                                  "https://en.wiktionary.org/wiki/@@@@").replace(
            "@@@@", self.word.text()
        )
        QDesktopServices.openUrl(QUrl(url))

    def onReaderOpen(self):
        url = f"http://{self.settings.value('reader_host', '127.0.0.1', type=str)}:{self.settings.value('reader_port', '39285', type=str)}"
        QDesktopServices.openUrl(QUrl(url))

    def lookupClicked(self, use_lemmatize=True):
        target = self.getCurrentWord()
        self.updateAnkiButtonState()
        if target == "":
            return
        self.lookupSet(target, use_lemmatize)

    def setState(self, state):
        self.word.setText(state['word'])
        self.definition.original = state['definition']
        display_mode1 = self.settings.value(
            self.settings.value("dict_source", "Wiktionary (English)")
            + "/display_mode",
            "Markdown-HTML"
        )
        skip_top1 = self.settings.value(
            self.settings.value("dict_source", "Wiktionary (English)")
            + "/skip_top",
            0, type=int
        )
        collapse_newlines1 = self.settings.value(
            self.settings.value("dict_source", "Wiktionary (English)")
            + "/collapse_newlines",
            0, type=int
        )
        if display_mode1 in ['Raw', 'Plaintext', 'Markdown']:
            self.definition.setPlainText(
                process_definition(
                    state['definition'].strip(),
                    display_mode1,
                    skip_top1,
                    collapse_newlines1
                )
            )
        else:
            self.definition.setHtml(
                process_definition(
                    state['definition'].strip(),
                    display_mode1,
                    skip_top1,
                    collapse_newlines1
                )
            )

        if state.get('definition2'):
            self.definition2.original = state['definition2']
            display_mode2 = self.settings.value(
                self.settings.value("dict_source2", "Wiktionary (English)")
                + "/display_mode",
                "Markdown-HTML"
            )
            skip_top2 = self.settings.value(
                self.settings.value("dict_source2", "Wiktionary (English)")
                + "/skip_top",
                0, type=int
            )
            collapse_newlines2 = self.settings.value(
                self.settings.value("dict_source2", "Wiktionary (English)")
                + "/collapse_newlines",
                0, type=int
            )
            if display_mode2 in ['Raw', 'Plaintext', 'Markdown']:
                self.definition2.setPlainText(
                    process_definition(
                        state['definition2'].strip(),
                        display_mode2,
                        skip_top2,
                        collapse_newlines2)
                )
            else:
                self.definition2.setHtml(
                    process_definition(
                        state['definition2'].strip(),
                        display_mode2,
                        skip_top2,
                        collapse_newlines2)
                )

        cursor = self.sentence.textCursor()
        cursor.clearSelection()
        self.sentence.setTextCursor(cursor)

    def setSentence(self, content):
        self.sentence.setText(str.strip(content))

    def setWord(self, content):
        self.word.setText(content)

    def setImage(self, content: Optional[QPixmap]):
        if content == None:
            self.image_viewer.setPixmap(QPixmap())
            self.image_viewer.setText("<center><b>&lt;No image selected&gt;</center>")
            self.image_path = None
            return
        filename = str(int(time.time()*1000)) + '.' + self.settings.value("img_format", "jpg")
        self.image_path = os.path.join(datapath, "images", filename)
        content.save(
            self.image_path, quality=self.settings.value("img_quality", -1, type=int)
        )
        self.image_viewer.setPixmap(content)

    def clipboardChanged(self, selection=False):
        """
        If the input is just a single word, we look it up right away.
        If it's a json and has the required fields, we use these fields to
        populate the relevant fields.
        Otherwise we dump everything to the Sentence field.
        By default this is not activated when the window is in focus to prevent
        mistakes, unless it is used from the button.
        """
        if selection:
            text = QApplication.clipboard().text(QClipboard.Selection)
        else:
            text = QApplication.clipboard().text()
        
        if not selection: 
            # I am not sure how you can copy an image to PRIMARY
            # so here we go
            if QApplication.clipboard().mimeData().hasImage():
                self.setImage(QApplication.clipboard().pixmap())
                return

        remove_spaces = self.settings.value("remove_spaces")
        lang = self.settings.value("target_language", "en")
        if is_json(text):
            copyobj = json.loads(text)
            target = copyobj['word']
            target = re.sub('[\\?\\.!«»…()\\[\\]]*', "", target)
            sentence = preprocess_clipboard(copyobj['sentence'], lang)
            if self.isActiveWindow() and sentence == self.sentence.toPlainText().replace("_", ""):
                return
            self.previousWord = target
            self.setSentence(sentence)
            self.setWord(target)
            self.lookupSet(target)
        elif self.single_word.isChecked() and is_oneword(preprocess_clipboard(text, lang)):
            self.setSentence(word := preprocess_clipboard(text, lang))
            self.setWord(word)
            self.lookupSet(text)
        else:
            self.setSentence(preprocess_clipboard(text, lang))

    def lookupSet(self, word, use_lemmatize=True):
        sentence_text = self.sentence.toPlainText()
        if self.settings.value("bold_word", True, type=bool):
            sentence_text = sentence_text.replace(
                "_", "").replace(word, f"__{word}__")
        self.sentence.setText(sentence_text)
        QCoreApplication.processEvents()
        result = self.lookup(word, use_lemmatize)
        self.setState(result)
        QCoreApplication.processEvents()
        self.audio_path = None
        if self.settings.value("audio_dict", "Forvo (all)") != "<disabled>":
            try:
                self.audios = getAudio(
                    word,
                    self.settings.value("target_language", 'en'),
                    dictionary=self.settings.value("audio_dict", "Forvo (all)"),
                    custom_dicts=json.loads(
                        self.settings.value("custom_dicts", '[]')))
            except Exception:
                self.audios = {}
            self.audio_selector.clear()
            if len(self.audios) > 0:
                for item in self.audios:
                    self.audio_selector.addItem("🔊 " + item)
                self.audio_selector.setCurrentItem(
                    self.audio_selector.item(0)
                )

    def lookup(self, word, use_lemmatize=True, record=True):
        """
        Look up a word and return a dict with the lemmatized form (if enabled)
        and definition
        """
        TL = self.settings.value("target_language", "en")
        lemmatize = use_lemmatize and self.settings.value(
            "lemmatization", True, type=bool)
        lemfreq = self.settings.value("lemfreq", True, type=bool)
        short_sign = "Y" if lemmatize else "N"
        language = TL  # This is in two letter code
        gtrans_lang = self.settings.value("gtrans_lang", "en")
        dictname = self.settings.value("dict_source", "Wiktionary (English)")
        freqname = self.settings.value("freq_source", "<disabled>")
        word = re.sub('[«»…,()\\[\\]_]*', "", word)
        if freqname != "<disabled>":
            freq_found = False
            try:
                freq, max_freq = getFreq(word, language, lemfreq, freqname)
                freq_found = True
            except TypeError:
                pass

            if freq_found:
                self.freq_display.setText(f'{str(freq)}/{str(max_freq)}')
                stars = freq_to_stars(freq)
                self.freq_stars_display.setText(stars)
            else:
                self.freq_display.setText("Frequency not found")
                self.freq_stars_display.setText("")
        if record:
            self.status(
                f"L: '{word}' in '{language}', lemma: {short_sign}, from {dictionaries.get(dictname, dictname)}")
        try:
            item = lookupin(
                word,
                language,
                lemmatize,
                dictname,
                gtrans_lang,
                self.settings.value("gtrans_api", "https://lingva.ml"))
            if record:
                self.rec.recordLookup(
                    word,
                    item['definition'],
                    TL,
                    lemmatize,
                    dictname,
                    True)
        except Exception as e:
            if record:
                self.status(str(e))
                self.rec.recordLookup(
                    word, None, TL, lemmatize, dictname, False)
                self.updateAnkiButtonState(True)
            item = {
                "word": word,
                "definition": failed_lookup(word, self.settings)
            }
            return item
        dict2name = self.settings.value("dict_source2", "<disabled>")
        if dict2name == "<disabled>":
            return item
        try:
            item2 = lookupin(word, language, lemmatize, dict2name, gtrans_lang)
            if record:
                self.rec.recordLookup(
                    word,
                    item['definition'],
                    TL,
                    lemmatize,
                    dict2name,
                    True)
        except Exception as e:
            self.status("Dict-2 failed" + str(e))
            if record:
                self.rec.recordLookup(
                    word, None, TL, lemmatize, dict2name, False)
            self.definition2.clear()
            return item
        return {
            "word": item['word'],
            'definition': item['definition'],
            'definition2': item2['definition']}

    def createNote(self):
        sentence = self.sentence.toPlainText().replace("\n", "<br>")
        frequency_stars = self.freq_stars_display.text()
        if self.settings.value("bold_word", True, type=bool):
            sentence = re.sub(
                r"__([ \w]+)__",
                r"<strong>\1</strong>",
                sentence)
        if self.settings.value("remove_spaces", False, type=bool):
            sentence = re.sub("\\s", "", sentence)
        tags = (self.settings.value("tags", "vocabsieve").strip() + " " + self.tags.text().strip()).split(" ")
        word = self.word.text()
        content = {
            "deckName": self.settings.value("deck_name"),
            "modelName": self.settings.value("note_type"),
            "fields": {
                self.settings.value("sentence_field"): sentence,
                self.settings.value("word_field"): word,
                self.settings.value("frequency_stars_field"): frequency_stars
            },
            "tags": tags
        }
        definition = self.process_defi_anki(
            self.definition,
            self.settings.value(
                self.settings.value("dict_source1", "Wiktionary (English)")
                + "/display_mode",
                "Markdown-HTML"
            )
        )
        content['fields'][self.settings.value('definition_field')] = definition
        definition2 = None
        if self.settings.value("dict_source2", "<disabled>") != '<disabled>':
            try:
                if self.settings.value(
                    "definition2_field",
                        "<disabled>") == "<disabled>":
                    self.warn(
                        "Aborted.\nYou must have field for Definition#2 in order to use two dictionaries.")
                    return
                definition2 = self.process_defi_anki(
                    self.definition2,
                    self.settings.value(
                        self.settings.value("dict_source2", "Wiktionary (English)")
                        + "/display_mode",
                        "Markdown-HTML"
                    )
                )
                content['fields'][self.settings.value(
                    'definition2_field')] = definition2
            except Exception as e:
                return

        if self.settings.value(
            "pronunciation_field",
                "<disabled>") != '<disabled>' and self.audio_path:
            content['audio'] = {
                "path": self.audio_path,
                "filename": os.path.basename(self.audio_path),
                "fields": [
                    self.settings.value("pronunciation_field")
                ]
            }
            self.audio_selector.clear()
        if self.settings.value("image_field", "<disabled>") != '<disabled>' and self.image_path:
            content['picture'] = {
                "path": self.image_path,
                "filename": os.path.basename(self.image_path),
                "fields": [
                    self.settings.value("image_field")
                ]
            }

        self.status("Adding note")
        api = self.settings.value("anki_api")
        try:
            if self.settings.value("enable_anki", True, type=bool):
                addNote(api, content)
                self.rec.recordNote(
                    json.dumps(content), 
                    sentence,
                    word,
                    definition,
                    definition2,
                    self.audio_path,
                    self.image_path,
                    " ".join(tags),
                    True
                )
            else:
                self.rec.recordNote(
                    json.dumps(content), 
                    sentence,
                    word,
                    definition,
                    definition2,
                    self.audio_path,
                    self.image_path,
                    " ".join(tags),
                    False
                )
            self.sentence.clear()
            self.word.clear()
            self.definition.clear()
            self.definition2.clear()
            self.status(f"Note added: '{word}'")
        except Exception as e:
            self.rec.recordNote(
                json.dumps(content), 
                sentence,
                word,
                definition,
                definition2,
                self.audio_path,
                self.image_path,
                " ".join(tags),
                False
            )
            self.status(f"Failed to add note: {word}")
            QMessageBox.warning(
                self,
                f"Failed to add note: {word}",
                "<h2>Failed to add note</h2>"
                + str(e)
                + "AnkiConnect must be running to add notes."
                "<br>If you wish to only add notes to the database (and "
                "export it as CSV), click Configure and uncheck 'Enable"
                " Anki' on the Anki tab."

            )
        self.setImage(None)

    def process_defi_anki(self, w: MyTextEdit, display_mode):
        "Process definitions before sending to Anki"
        if display_mode in ["Raw", "Plaintext"]:
            return w.toPlainText()
        elif display_mode == "Markdown":
            return markdown_nop(w.toPlainText())
        elif display_mode == "Markdown-HTML":
            return markdown_nop(w.toMarkdown())
        elif display_mode == "HTML":
            return w.original

    def errorNoConnection(self, error):
        """
        Dialog window sent when something goes wrong in configuration step
        """
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Critical)
        msg.setText("Error")
        msg.setInformativeText(
            str(error) +
            "\n\nHints:" +
            "\nAnkiConnect must be running in order to add notes." +
            "\nIf you have AnkiConnect running at an alternative endpoint," +
            "\nbe sure to change it in the configuration.")
        msg.exec()

    def initTimer(self):
        self.showStats()
        self.timer = QTimer()
        self.timer.timeout.connect(self.showStats)
        self.timer.start(2000)

    def showStats(self):
        lookups = self.rec.countLookupsToday()
        notes = self.rec.countNotesToday()
        self.stats_label.setText(f"L:{str(lookups)} N:{str(notes)}")

    def time(self):
        return QDateTime.currentDateTime().toString('[hh:mm:ss]')

    def status(self, msg):
        self.bar.showMessage(self.time() + " " + msg, 4000)

    def warn(self, text):
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Warning)
        msg.setText(text)
        msg.exec()

    def startServer(self):
        if self.settings.value("api_enabled", True, type=bool):
            try:
                self.thread = QThread()
                port = self.settings.value("port", 39284, type=int)
                host = self.settings.value("host", "127.0.0.1")
                self.worker = LanguageServer(self, host, port)
                self.worker.moveToThread(self.thread)
                self.thread.started.connect(self.worker.start_api)
                self.worker.note_signal.connect(self.onNoteSignal)
                self.thread.start()
            except Exception as e:
                print(e)
                self.status("Failed to start API server")
        if self.settings.value("reader_enabled", True, type=bool):
            try:
                self.thread2 = QThread()
                port = self.settings.value("reader_port", 39285, type=int)
                host = self.settings.value("reader_host", "127.0.0.1")
                self.worker2 = ReaderServer(self, host, port)
                self.worker2.moveToThread(self.thread2)
                self.thread2.started.connect(self.worker2.start_api)
                self.thread2.start()
            except Exception as e:
                print(e)
                self.status("Failed to start reader server")

    def onNoteSignal(
            self,
            sentence: str,
            word: str,
            definition: str,
            tags: list):
        self.setSentence(sentence)
        self.setWord(word)
        self.definition.setText(definition)
        self.tags.setText(" ".join(tags))
        self.createNote()


class AboutDialog(QDialog):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("HELLO!")

        QBtn = QDialogButtonBox.Ok

        self.buttonBox = QDialogButtonBox(QBtn)
        self.buttonBox.accepted.connect(self.accept)

        self.layout = QVBoxLayout()
        message = QLabel(
            '''
© 2022 FreeLanguageTools<br><br>
Visit <a href="https://freelanguagetools.org">FreeLanguageTools.org</a> for more info on how to use this tool.<br>
You can also talk to us on <a href="https://webchat.kde.org/#/room/#flt:midov.pl">Matrix</a>
or <a href="https://t.me/fltchat">Telegram</a> for support.<br><br>

Consult <a href="https://freelanguagetools.org/2021/08/dictionaries-and-frequency-lists-for-ssm/">this link</a>
to find compatible dictionaries. <br><br>

VocabSieve (formerly SSM, ssmtool) is free software available to you under the terms of
<a href="https://www.gnu.org/licenses/gpl-3.0.en.html">GNU GPLv3</a>.
If you found a bug, or have enhancement ideas, please open an issue on the
Github <a href=https://github.com/FreeLanguageTools/vocabsieve>repository</a>.<br><br>

This program is yours to keep. There is no EULA you need to agree to.
No data is sent to any server other than the configured dictionary APIs.
Statistics data are stored locally.
<br><br>
Credits: <br><a href="https://en.wiktionary.org/wiki/Wiktionary:Main_Page">Wiktionary API</a><br>
If you find this tool useful, you can give it a star on Github and tell others about it. Any suggestions will also be appreciated.
            '''
        )
        message.setTextFormat(Qt.RichText)
        message.setOpenExternalLinks(True)
        message.setWordWrap(True)
        message.adjustSize()
        self.layout.addWidget(message)
        self.layout.addWidget(self.buttonBox)
        self.setLayout(self.layout)


def main():
    app = QApplication(sys.argv)
    w = DictionaryWindow()

    w.show()
    sys.exit(app.exec())
