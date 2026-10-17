import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui

// Bar button + popup listing accent colors matugen finds in the current
// background. Picking one changes only the current theme's accent
// (accent.py); "Theme default" puts the theme's own accent back. While a
// picked accent is in place, a background change re-applies the color
// remembered for the new background.
Panel {
  id: root
  moduleName: "worstpoet.accent-picker"
  ipcTarget: "worstpoet.accent-picker"

  readonly property string script: decodeURIComponent(String(Qt.resolvedUrl("accent.py")).replace(/^file:\/\//, ""))
  readonly property color fg: root.bar ? root.bar.foreground : Commons.Color.foreground
  readonly property string fontFamily: root.bar ? root.bar.fontFamily : Style.font.family

  // Row 0 is "Theme default"; rows 1.. are the background's candidates.
  property var colors: []
  property var activeIndex: null
  property string themeName: ""
  property string themeAccent: ""
  property string error: ""
  property bool loading: false
  property bool applying: false
  property int cursor: 0
  property bool cursorActive: false
  property string lastBackground: ""

  readonly property var rows: [{ index: -1, name: "Theme default", accent: root.themeAccent }].concat(root.colors)

  function refresh() {
    if (listProc.running) return
    root.loading = root.colors.length === 0
    root.error = ""
    listProc.command = ["python3", root.script, "list"]
    listProc.running = true
  }

  function pick(index) {
    if (applyProc.running) return
    root.applying = true
    root.error = ""
    applyProc.command = index < 0
      ? ["python3", root.script, "reset"]
      : ["python3", root.script, "apply", String(index)]
    applyProc.running = true
  }

  function moveCursor(delta) {
    root.cursor = (root.cursor + delta + root.rows.length) % root.rows.length
  }

  function parse(raw) {
    try { return JSON.parse(String(raw || "").trim().split("\n").pop()) } catch (e) { return null }
  }

  onOpenedChanged: {
    if (!opened) return
    refresh()
    root.cursorActive = false
  }

  Process {
    id: listProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var r = root.parse(text)
        root.loading = false
        if (!r) { root.error = "Couldn't read the colors"; return }
        if (!r.ok) { root.error = r.error; root.colors = []; return }
        root.colors = r.colors
        root.activeIndex = r.active
        root.themeName = r.theme
        root.themeAccent = r.themeAccent || ""
        root.cursor = 0
        for (var i = 0; i < r.colors.length; i++)
          if (r.colors[i].index === r.active) root.cursor = i + 1
      }
    }
  }

  Process {
    id: applyProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var r = root.parse(text)
        if (r && r.ok) root.activeIndex = r.index
        else root.error = r ? r.error : "Couldn't change the accent"
      }
    }
    onExited: root.applying = false
  }

  // No hook fires on a background change, so poll the symlink with a cheap
  // readlink and only start Python when it actually changed.
  Process {
    id: bgProc
    command: ["readlink", "-f", Quickshell.env("HOME") + "/.local/state/omarchy/current/background"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var bg = String(text || "").trim()
        if (bg === "" || bg === root.lastBackground) return
        var first = root.lastBackground === ""
        root.lastBackground = bg
        if (first) return
        if (!syncProc.running && !applyProc.running) syncProc.running = true
        if (root.opened) root.refresh()
      }
    }
  }

  Process {
    id: syncProc
    command: ["python3", root.script, "sync"]
  }

  Timer {
    interval: 4000
    running: setting("followBackground", true) === true
    repeat: true
    triggeredOnStart: true
    onTriggered: if (!bgProc.running) bgProc.running = true
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󰏘"
    tooltipText: "Accent color"
    onPressed: function(b) { root.toggle() }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: panel.loadedContent
    contentWidth: panel.fittedContentWidth(Style.space(300))
    contentHeight: panel.fittedContentHeight(panel.loadedContentHeight)

    content: PanelKeyCatcher {
      anchors.fill: parent
      implicitHeight: column.implicitHeight
      onMoveRequested: function(dx, dy) {
        if (!root.cursorActive) { root.cursorActive = true; return }
        root.moveCursor(dx !== 0 ? dx : dy)
      }
      onActivateRequested: {
        if (root.cursorActive && root.colors.length > 0) root.pick(root.rows[root.cursor].index)
      }
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Column {
        id: column
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        spacing: Style.space(10)

        PanelSectionHeader {
          text: root.themeName !== "" ? "Accent for " + root.themeName : "Accent color"
          foreground: root.fg
          fontFamily: root.fontFamily
        }

        Text {
          visible: root.loading || root.applying || root.error !== ""
          width: parent.width
          wrapMode: Text.Wrap
          textFormat: Text.PlainText
          text: root.error !== "" ? root.error
            : root.applying ? "Applying…"
            : "Reading colors from the background…"
          color: root.error !== "" ? Commons.Color.urgent : root.fg
          opacity: root.error !== "" ? 1 : 0.6
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
        }

        Repeater {
          model: root.colors.length > 0 ? root.rows : []

          Rectangle {
            id: row
            required property var modelData
            required property int index
            readonly property bool isDefault: modelData.index < 0
            readonly property bool active: isDefault ? root.activeIndex === null : root.activeIndex === modelData.index
            readonly property bool hasCursor: (root.cursorActive && root.cursor === index) || mouse.containsMouse

            width: column.width
            height: Style.space(40)
            radius: Style.cornerRadius
            color: hasCursor ? Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.08) : "transparent"
            border.width: active ? Math.max(1, Style.space(2)) : 0
            border.color: modelData.accent || root.fg

            Rectangle {
              id: swatch
              anchors.left: parent.left
              anchors.leftMargin: Style.space(10)
              anchors.verticalCenter: parent.verticalCenter
              width: Style.space(24)
              height: width
              radius: width / 2
              color: row.modelData.accent || "transparent"
              border.width: row.isDefault ? 1 : 0
              border.color: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.4)
            }

            Text {
              anchors.left: swatch.right
              anchors.leftMargin: Style.space(12)
              anchors.verticalCenter: parent.verticalCenter
              textFormat: Text.PlainText
              text: row.modelData.name
              color: root.fg
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
              font.bold: row.active
            }

            Text {
              anchors.right: parent.right
              anchors.rightMargin: Style.space(10)
              anchors.verticalCenter: parent.verticalCenter
              textFormat: Text.PlainText
              text: row.active ? "✓" : (row.modelData.accent || "")
              color: root.fg
              opacity: row.active ? 1 : 0.5
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }

            MouseArea {
              id: mouse
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: Qt.PointingHandCursor
              onEntered: { root.cursorActive = true; root.cursor = row.index }
              onClicked: root.pick(row.modelData.index)
            }
          }
        }
      }
    }
  }
}
