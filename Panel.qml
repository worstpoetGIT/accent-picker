import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui

// Bar button + popup listing the source colors matugen finds in the current
// background. Picking one builds the "accent-picker" Omarchy theme from it
// (accent.py) and switches to it without touching the background. While that
// theme is active, a background change re-applies the color remembered for
// the new background.
Panel {
  id: root
  moduleName: "worstpoet.accent-picker"
  ipcTarget: "worstpoet.accent-picker"

  readonly property string script: decodeURIComponent(String(Qt.resolvedUrl("accent.py")).replace(/^file:\/\//, ""))
  readonly property string mode: setting("mode", "Dark") === "Light" ? "light" : "dark"
  readonly property color fg: root.bar ? root.bar.foreground : Commons.Color.foreground
  readonly property string fontFamily: root.bar ? root.bar.fontFamily : Style.font.family

  property var colors: []
  property var activeIndex: null
  property string error: ""
  property bool loading: false
  property bool applying: false
  property int cursor: 0
  property bool cursorActive: false
  property string lastBackground: ""

  function refresh() {
    if (listProc.running) return
    root.loading = root.colors.length === 0
    root.error = ""
    listProc.command = ["python3", root.script, "list", root.mode]
    listProc.running = true
  }

  function pick(index) {
    if (applyProc.running) return
    root.applying = true
    root.error = ""
    applyProc.command = ["python3", root.script, "apply", String(index), root.mode]
    applyProc.running = true
  }

  function moveCursor(delta) {
    if (root.colors.length === 0) return
    root.cursor = (root.cursor + delta + root.colors.length) % root.colors.length
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
        if (!r) { root.error = "Couldn't read the palette"; return }
        if (!r.ok) { root.error = r.error; root.colors = []; return }
        root.colors = r.colors
        root.activeIndex = r.active
        for (var i = 0; i < r.colors.length; i++)
          if (r.colors[i].index === r.active) root.cursor = i
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
        else root.error = r ? r.error : "Couldn't apply the accent"
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
    command: ["python3", root.script, "sync", root.mode]
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
    contentWidth: panel.fittedContentWidth(Style.space(320))
    contentHeight: panel.fittedContentHeight(panel.loadedContentHeight)

    content: PanelKeyCatcher {
      anchors.fill: parent
      implicitHeight: column.implicitHeight
      onMoveRequested: function(dx, dy) {
        if (!root.cursorActive) { root.cursorActive = true; return }
        root.moveCursor(dx !== 0 ? dx : dy)
      }
      onActivateRequested: {
        if (root.cursorActive && root.colors.length > 0) root.pick(root.colors[root.cursor].index)
      }
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Column {
        id: column
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        spacing: Style.space(12)

        PanelSectionHeader {
          text: "Accent from background"
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
          model: root.colors

          Rectangle {
            id: row
            required property var modelData
            required property int index
            readonly property bool active: root.activeIndex === modelData.index
            readonly property bool hasCursor: (root.cursorActive && root.cursor === index) || mouse.containsMouse

            width: column.width
            height: Style.space(44)
            radius: Style.cornerRadius
            color: hasCursor ? Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.08) : "transparent"
            border.width: active ? Math.max(1, Style.space(2)) : 0
            border.color: modelData.accent

            Row {
              anchors.left: parent.left
              anchors.leftMargin: Style.space(10)
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(4)

              Repeater {
                model: [row.modelData.accent, row.modelData.secondary, row.modelData.tertiary]
                Rectangle {
                  required property var modelData
                  required property int index
                  width: index === 0 ? Style.space(28) : Style.space(14)
                  height: Style.space(28)
                  radius: Style.space(6)
                  color: modelData
                }
              }
            }

            Text {
              anchors.left: parent.left
              anchors.leftMargin: Style.space(84)
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
              text: row.active ? "✓" : row.modelData.accent
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
