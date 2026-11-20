"""Socket and chip placement preview, retained from the original GUI sketch."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QFont, QResizeEvent
from PyQt6.QtWidgets import QGraphicsEllipseItem, QGraphicsRectItem, QGraphicsScene, QGraphicsTextItem, QGraphicsView, QWidget


class SocketPreviewWidget(QGraphicsView):
    def __init__(self, chip_pins: int = 14, socket_position: int = 1, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.dpi = self.logicalDpiX()
        self.chip_pins: int | None = chip_pins
        self.socket_position = socket_position
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.set_chip(chip_pins, socket_position)

    def set_chip(self, chip_pins: int | None, socket_position: int = 1) -> None:
        if chip_pins is not None and (chip_pins not in (8, 14, 18, 28, 40) or not 1 <= socket_position <= 21 - chip_pins // 2):
            msg = 'Chip pin count or socket position is invalid.'
            raise ValueError(msg)
        self.chip_pins = chip_pins
        self.socket_position = socket_position
        self._scene.clear()
        self.draw_socket()
        if chip_pins is not None:
            self.draw_chip()
        self.fit_socket_to_view()

    def mm_to_px(self, mm: float) -> float:
        return (mm / 25.4) * self.dpi

    def resizeEvent(self, event: QResizeEvent | None) -> None:  # noqa: N802 - Qt virtual method
        super().resizeEvent(event)
        self.fit_socket_to_view()

    def fit_socket_to_view(self) -> None:
        items_bbox = self._scene.itemsBoundingRect()
        padding_ratio = 0.1
        pad_w = items_bbox.width() * padding_ratio
        pad_h = items_bbox.height() * padding_ratio
        padded_bbox = items_bbox.adjusted(-pad_w, -pad_h, pad_w, pad_h)
        self.fitInView(padded_bbox, Qt.AspectRatioMode.KeepAspectRatio)

    def draw_socket(self) -> None:
        start_x = 50
        start_y = 20

        number_of_pins_in_row = 20
        number_of_rows = 2
        pin_height = self.mm_to_px(1.3)
        pin_width = self.mm_to_px(7)
        vertical_spacing = self.mm_to_px(1.1)
        horizontal_spacing = self.mm_to_px(4)
        horizontal_padding = self.mm_to_px(2.1)
        bottom_padding = self.mm_to_px(6.5)
        top_padding = self.mm_to_px(8.5)
        leaver_length = self.mm_to_px(17)
        leaver_thickens = self.mm_to_px(1.5)
        leaver_top_offset = self.mm_to_px(11)

        # Drawn socket body
        socket_width = pin_width * number_of_rows + horizontal_spacing + (horizontal_padding * 2)
        socket_height = (pin_height + vertical_spacing) * number_of_pins_in_row - vertical_spacing
        socket = QGraphicsRectItem(start_x, start_y+leaver_top_offset, socket_width, socket_height + top_padding + bottom_padding)
        socket.setBrush(QBrush(Qt.GlobalColor.darkGreen))
        self._scene.addItem(socket)

        # Drawn leaver
        leaver = QGraphicsRectItem(start_x, start_y, leaver_thickens, leaver_length)
        leaver.setBrush(QBrush(Qt.GlobalColor.lightGray))
        self._scene.addItem(leaver)

        for i in range(number_of_pins_in_row):
            for col in range(number_of_rows):
                pin_num = i + 1 if col == 0 else 40 - i
                x = start_x + col * (pin_width + horizontal_spacing) + horizontal_padding
                y = start_y + top_padding + leaver_top_offset + i * (pin_height + vertical_spacing)
                pin = QGraphicsRectItem(x, y, pin_width, pin_height)
                pin.setBrush(QBrush(Qt.GlobalColor.lightGray))
                self._scene.addItem(pin)

                label = QGraphicsTextItem(str(pin_num))
                font = QFont()
                font.setPointSize(round(self.mm_to_px(1.8)))  # Set desired point size
                label.setFont(font)
                text_rect = label.boundingRect()
                x_pos = x - text_rect.width() - horizontal_padding if col == 0 else x + pin_width + horizontal_padding
                label.setPos(x_pos, y + (pin_height - text_rect.height()) / 2)
                self._scene.addItem(label)

        # After setting up your scene:
        self.fit_socket_to_view()

    def draw_chip(self) -> None:
        number_of_rows = 2
        pin_width = self.mm_to_px(7)
        pin_height = self.mm_to_px(1.3)
        horizontal_spacing = self.mm_to_px(4)
        horizontal_padding = self.mm_to_px(2.1)
        vertical_spacing = self.mm_to_px(1.1)
        leaver_top_offset = self.mm_to_px(11)
        top_padding = self.mm_to_px(8.5)
        chip_key_size = self.mm_to_px(1)
        chip_key_padding = self.mm_to_px(0.5)
        socket_width = pin_width * number_of_rows + horizontal_spacing + (horizontal_padding * 2)

        if self.chip_pins is None:
            return
        pins_per_side = self.chip_pins // 2
        chip_height = (pin_height + vertical_spacing) * pins_per_side

        chip_width = self.mm_to_px(16 if self.chip_pins == 40 else 6.15)

        start_pin = self.socket_position

        start_x = 50
        start_y = 20

        # Calculate vertical offset based on socket pin alignment
        # We offset by vertical_spacing / 2 to top since chip_height have 1* vertical_spacing more and this is a good
        # way to render chip body overlying pins
        chip_offset = ((start_pin - 1) % 20) * (pin_height + vertical_spacing) + leaver_top_offset + top_padding - vertical_spacing / 2

        # Set chip on center of socket in X dimension
        x_position = start_x + (socket_width / 2) - (chip_width / 2)

        chip_rect = QGraphicsRectItem(x_position, start_y + chip_offset, chip_width, chip_height)
        chip_rect.setBrush(QBrush(Qt.GlobalColor.black))
        self._scene.addItem(chip_rect)

        # Render chip pin 1 key
        chip_key = QGraphicsEllipseItem(x_position + chip_key_padding, start_y + chip_offset + chip_key_padding, chip_key_size, chip_key_size)
        chip_key.setBrush(QBrush(Qt.GlobalColor.gray))
        self._scene.addItem(chip_key)
