#!/usr/bin/env python

# Copyright (c) 2014, Austin Hendrix
# Copyright (c) 2011, Dorian Scholz, TU Darmstadt
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
#   * Redistributions of source code must retain the above copyright
#     notice, this list of conditions and the following disclaimer.
#   * Redistributions in binary form must reproduce the above
#     copyright notice, this list of conditions and the following
#     disclaimer in the documentation and/or other materials provided
#     with the distribution.
#   * Neither the name of the TU Darmstadt nor the names of its
#     contributors may be used to endorse or promote products derived
#     from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

import numpy

from qt_gui_py_common.simple_settings_dialog import SimpleSettingsDialog
from python_qt_binding import QT_BINDING
from python_qt_binding.QtCore import Qt, qVersion, qWarning, Signal
from python_qt_binding.QtGui import QColor
from python_qt_binding.QtWidgets import QWidget, QHBoxLayout
from rqt_py_common.ini_helper import pack, unpack

try:
    from .mat_data_plot import MatDataPlot
except ImportError as e:
    MatDataPlot = None

# separate class for DataPlot exceptions, just so that users can differentiate
# errors from the DataPlot widget from exceptions generated by the underlying
# libraries
class DataPlotException(Exception):
    pass

class DataPlot(QWidget):
    """A widget for displaying a plot of data

    The DataPlot widget displays a plot, on one of several plotting backends,
    depending on which backend(s) are available at runtime. It currently 
    supports PyQtGraph, MatPlot and QwtPlot backends.

    The DataPlot widget manages the plot backend internally, and can save
    and restore the internal state using `save_settings` and `restore_settings`
    functions.

    Currently, the user MUST call `restore_settings` before using the widget,
    to cause the creation of the enclosed plotting widget.
    """
    # plot types in order of priority
    plot_types = [
        {
            'title': 'MatPlot',
            'widget_class': MatDataPlot,
            'description': 'Based on MatPlotLib\n- needs most CPU\n- needs matplotlib >= 1.1.0\n- if using PySide: PySide > 1.1.0\n',
            'enabled': MatDataPlot is not None,
        },
    ]

    # pre-defined colors:
    RED=(255, 0, 0)
    GREEN=(0, 255, 0)
    BLUE=(0, 0, 255)

    SCALE_ALL=1
    SCALE_VISIBLE=2
    SCALE_EXTEND=4

    _colors = [Qt.blue, Qt.red, Qt.cyan, Qt.magenta, Qt.green, Qt.darkYellow, Qt.black, Qt.darkCyan, Qt.darkRed, Qt.gray]

    limits_changed = Signal()
    _redraw = Signal()
    _add_curve = Signal(str, str, 'QColor', bool)

    def __init__(self, parent=None):
        """Create a new, empty DataPlot

        This will raise a RuntimeError if none of the supported plotting
        backends can be found
        """
        super(DataPlot, self).__init__(parent)
        self._plot_index = 0
        self._color_index = 0
        self._markers_on = False
        self._autoscroll = True

        self._autoscale_x = True
        self._autoscale_y = DataPlot.SCALE_ALL

        # the backend widget that we're trying to hide/abstract
        self._data_plot_widget = None
        self._curves = {}
        self._vline = None
        self._redraw.connect(self._do_redraw)

        self._layout = QHBoxLayout()
        self.setLayout(self._layout)

        enabled_plot_types = [pt for pt in self.plot_types if pt['enabled']]
        if not enabled_plot_types:
            if qVersion().startswith('4.'):
                version_info = '1.1.0'
            else:
                # minimum matplotlib version for Qt 5
                version_info = '1.4.0'
            if QT_BINDING == 'pyside':
                version_info += ' and PySide %s' % \
                    ('> 1.1.0' if qVersion().startswith('4.') else '>= 2.0.0')
            raise RuntimeError('No usable plot type found. Install at least one of: PyQtGraph, MatPlotLib (at least %s) or Python-Qwt5.' % version_info)

        self._switch_data_plot_widget(self._plot_index)

        self.show()

    def _switch_data_plot_widget(self, plot_index, markers_on=False):
        """Internal method for activating a plotting backend by index"""
        # check if selected plot type is available
        if not self.plot_types[plot_index]['enabled']:
            # find other available plot type
            for index, plot_type in enumerate(self.plot_types):
                if plot_type['enabled']:
                    plot_index = index
                    break

        self._plot_index = plot_index
        self._markers_on = markers_on
        selected_plot = self.plot_types[plot_index]

        if self._data_plot_widget:
            x_limits = self.get_xlim()
            y_limits = self.get_ylim()

            self._layout.removeWidget(self._data_plot_widget)
            self._data_plot_widget.close()
            self._data_plot_widget = None
        else:
            x_limits = [0.0, 10.0]
            y_limits = [-0.001, 0.001]

        self._data_plot_widget = selected_plot['widget_class'](self)
        self._data_plot_widget.limits_changed.connect(self.limits_changed)
        self._add_curve.connect(self._data_plot_widget.add_curve)
        self._layout.addWidget(self._data_plot_widget)

        # restore old data
        for curve_id in self._curves:
            curve = self._curves[curve_id]
            self._data_plot_widget.add_curve(curve_id, curve['name'], curve['color'], markers_on)

        if self._vline:
            self.vline(*self._vline)

        self.set_xlim(x_limits)
        self.set_ylim(y_limits)
        self.redraw()

    def _switch_plot_markers(self, markers_on):
        self._markers_on = markers_on
        self._data_plot_widget._color_index = 0

        for curve_id in self._curves:
            self._data_plot_widget.remove_curve(curve_id)
            curve = self._curves[curve_id]
            self._data_plot_widget.add_curve(curve_id, curve['name'], curve['color'], markers_on)

        self.redraw()

    # interface out to the managing GUI component: get title, save, restore, 
    # etc
    def getTitle(self):
        """get the title of the current plotting backend"""
        return self.plot_types[self._plot_index]['title']

    def save_settings(self, plugin_settings, instance_settings):
        """Save the settings associated with this widget

        Currently, this is just the plot type, but may include more useful
        data in the future"""
        instance_settings.set_value('plot_type', self._plot_index)
        xlim = self.get_xlim()
        ylim = self.get_ylim()
        # convert limits to normal arrays of floats; some backends return numpy
        # arrays
        xlim = [float(x) for x in xlim]
        ylim = [float(y) for y in ylim]
        instance_settings.set_value('x_limits', pack(xlim))
        instance_settings.set_value('y_limits', pack(ylim))

    def restore_settings(self, plugin_settings, instance_settings):
        """Restore the settings for this widget

        Currently, this just restores the plot type."""
        self._switch_data_plot_widget(int(instance_settings.value('plot_type', 0)))
        xlim = unpack(instance_settings.value('x_limits', []))
        ylim = unpack(instance_settings.value('y_limits', []))
        if xlim:
            # convert limits to an array of floats; they're often lists of
            # strings
            try:
                xlim = [float(x) for x in xlim]
                self.set_xlim(xlim)
            except:
                qWarning("Failed to restore X limits")
        if ylim:
            try:
                ylim = [float(y) for y in ylim]
                self.set_ylim(ylim)
            except:
                qWarning("Failed to restore Y limits")


    def doSettingsDialog(self):
        """Present the user with a dialog for choosing the plot backend

        This displays a SimpleSettingsDialog asking the user to choose a
        plot type, gets the result, and updates the plot type as necessary
        
        This method is blocking"""

        marker_settings = [
            {
                'title': 'Show Plot Markers',
                'description': 'Warning: Displaying markers in rqt_plot may cause\n \t high cpu load, especially using PyQtGraph\n',
                'enabled': True,
            }]
        if self._markers_on:
            selected_checkboxes = [0]
        else:
            selected_checkboxes = []

        dialog = SimpleSettingsDialog(title='Plot Options')
        dialog.add_exclusive_option_group(title='Plot Type', options=self.plot_types, selected_index=self._plot_index)
        dialog.add_checkbox_group(title='Plot Markers', options=marker_settings, selected_indexes=selected_checkboxes)
        [plot_type, checkboxes] = dialog.get_settings()
        if plot_type is not None and plot_type['selected_index'] is not None and self._plot_index != plot_type['selected_index']:
            self._switch_data_plot_widget(plot_type['selected_index'], 0 in checkboxes['selected_indexes'])
        else:
            if checkboxes is not None and self._markers_on != (0 in checkboxes['selected_indexes']):
                self._switch_plot_markers(0 in checkboxes['selected_indexes'])

    # interface out to the managing DATA component: load data, update data,
    # etc
    def autoscroll(self, enabled=True):
        """Enable or disable autoscrolling of the plot"""
        self._autoscroll = enabled

    def redraw(self):
        self._redraw.emit()

    def _do_redraw(self):
        """Redraw the underlying plot

        This causes the underlying plot to be redrawn. This is usually used
        after adding or updating the plot data"""
        if self._data_plot_widget:
            self._merged_autoscale()
            for curve_id in self._curves:
                curve = self._curves[curve_id]
                self._data_plot_widget.set_values(curve_id, curve['x'], curve['y'])
            self._data_plot_widget.redraw()

    def _get_curve(self, curve_id):
        if curve_id in self._curves:
            return self._curves[curve_id]
        else:
            raise DataPlotException("No curve named %s in this DataPlot" %
                    ( curve_id) )

    def add_curve(self, curve_id, curve_name, data_x, data_y):
        """Add a new, named curve to this plot

        Add a curve named `curve_name` to the plot, with initial data series
        `data_x` and `data_y`.
        
        Future references to this curve should use the provided `curve_id`

        Note that the plot is not redraw automatically; call `redraw()` to make
        any changes visible to the user.
        """
        curve_color = QColor(self._colors[self._color_index % len(self._colors)])
        self._color_index += 1

        self._curves[curve_id] = { 'x': numpy.array(data_x),
                                   'y': numpy.array(data_y),
                                   'name': curve_name,
                                   'color': curve_color}
        if self._data_plot_widget:
            self._add_curve.emit(curve_id, curve_name, curve_color, self._markers_on)

    def remove_curve(self, curve_id):
        """Remove the specified curve from this plot"""
        # TODO: do on UI thread with signals
        if curve_id in self._curves:
            del self._curves[curve_id]
        if self._data_plot_widget:
            self._data_plot_widget.remove_curve(curve_id)

    def update_values(self, curve_id, values_x, values_y, sort_data=True):
        """Append new data to an existing curve
        
        `values_x` and `values_y` will be appended to the existing data for
        `curve_id`

        Note that the plot is not redraw automatically; call `redraw()` to make
        any changes visible to the user.

        If `sort_data` is set to False, values won't be sorted by `values_x`
        order.
        """
        curve = self._get_curve(curve_id)
        curve['x'] = numpy.append(curve['x'], values_x)
        curve['y'] = numpy.append(curve['y'], values_y)

        if sort_data:
            # sort resulting data, so we can slice it later
            sort_order = curve['x'].argsort()
            curve['x'] = curve['x'][sort_order]
            curve['y'] = curve['y'][sort_order]

    def clear_values(self, curve_id=None):
        """Clear the values for the specified curve, or all curves

        This will erase the data series associaed with `curve_id`, or all
        curves if `curve_id` is not present or is None

        Note that the plot is not redraw automatically; call `redraw()` to make
        any changes visible to the user.
        """
        # clear internal curve representation
        if curve_id:
            curve = self._get_curve(curve_id)
            curve['x'] = numpy.array([])
            curve['y'] = numpy.array([])
        else:
            for curve_id in self._curves:
                self._curves[curve_id]['x'] = numpy.array([])
                self._curves[curve_id]['y'] = numpy.array([])


    def vline(self, x, color=RED):
        """Draw a vertical line on the plot

        Draw a line a position X, with the given color
        
        @param x: position of the vertical line to draw
        @param color: optional parameter specifying the color, as tuple of
                      RGB values from 0 to 255
        """
        self._vline = (x, color)
        if self._data_plot_widget:
            self._data_plot_widget.vline(x, color)

    # autoscaling methods
    def set_autoscale(self, x=None, y=None):
        """Change autoscaling of plot axes

        if a parameter is not passed, the autoscaling setting for that axis is
        not changed

        @param x: enable or disable autoscaling for X
        @param y: set autoscaling mode for Y
        """
        if x is not None:
            self._autoscale_x = x
        if y is not None:
            self._autoscale_y = y

    # autoscaling:  adjusting the plot bounds fit the data
    # autoscrollig: move the plot X window to show the most recent data
    #
    # what order do we do these adjustments in?
    #  * assuming the various stages are enabled:
    #  * autoscale X to bring all data into view
    #   * else, autoscale X to determine which data we're looking at
    #  * autoscale Y to fit the data we're viewing
    #
    # * autoscaling of Y might have several modes:
    #  * scale Y to fit the entire dataset
    #  * scale Y to fit the current view
    #  * increase the Y scale to fit the current view
    #
    # TODO: incrmenetal autoscaling: only update the autoscaling bounds
    #       when new data is added
    def _merged_autoscale(self):
        x_limit = [numpy.inf, -numpy.inf]
        if self._autoscale_x:
            for curve_id in self._curves:
                curve = self._curves[curve_id]
                if len(curve['x']) > 0:
                    x_limit[0] = min(x_limit[0], curve['x'].min())
                    x_limit[1] = max(x_limit[1], curve['x'].max())
        elif self._autoscroll:
            # get current width of plot
            x_limit = self.get_xlim()
            x_width = x_limit[1] - x_limit[0]

            # reset the upper x_limit so that we ignore the previous position
            x_limit[1] = -numpy.inf
            
            # get largest X value
            for curve_id in self._curves:
                curve = self._curves[curve_id]
                if len(curve['x']) > 0:
                    x_limit[1] = max(x_limit[1], curve['x'].max())

            # set lower limit based on width
            x_limit[0] = x_limit[1] - x_width
        else:
            # don't modify limit, or get it from plot
            x_limit = self.get_xlim()

        # set sane limits if our limits are infinite
        if numpy.isinf(x_limit[0]):
            x_limit[0] = 0.0
        if numpy.isinf(x_limit[1]):
            x_limit[1] = 1.0


        y_limit = [numpy.inf, -numpy.inf]
        if self._autoscale_y:
            # if we're extending the y limits, initialize them with the
            # current limits
            if self._autoscale_y & DataPlot.SCALE_EXTEND:
                y_limit = self.get_ylim()
            for curve_id in self._curves:
                curve = self._curves[curve_id]
                start_index = 0
                end_index = len(curve['x'])

                # if we're scaling based on the visible window, find the
                # start and end indicies of our window
                if self._autoscale_y & DataPlot.SCALE_VISIBLE:
                    # indexof x_limit[0] in curves['x']
                    start_index = curve['x'].searchsorted(x_limit[0])
                    # indexof x_limit[1] in curves['x']
                    end_index = curve['x'].searchsorted(x_limit[1])

                # region here is cheap because it is a numpy view and not a
                # copy of the underlying data
                region = curve['y'][start_index:end_index]
                if len(region) > 0:
                    y_limit[0] = min(y_limit[0], region.min())
                    y_limit[1] = max(y_limit[1], region.max())

                # TODO: compute padding around new min and max values
                #       ONLY consider data for new values; not
                #       existing limits, or we'll add padding on top of old
                #       padding in SCALE_EXTEND mode
                # 
                # pad the min/max
                # TODO: invert this padding in get_ylim
                #ymin = limits[0]
                #ymax = limits[1]
                #delta = ymax - ymin if ymax != ymin else 0.1
                #ymin -= .05 * delta
                #ymax += .05 * delta
        else:
            y_limit = self.get_ylim()

        # set sane limits if our limits are infinite
        if numpy.isinf(y_limit[0]):
            y_limit[0] = 0.0
        if numpy.isinf(y_limit[1]):
            y_limit[1] = 1.0

        self.set_xlim(x_limit)
        self.set_ylim(y_limit)

    def get_xlim(self):
        """get X limits"""
        if self._data_plot_widget:
            return self._data_plot_widget.get_xlim()
        else:
            qWarning("No plot widget; returning default X limits")
            return [0.0, 1.0]

    def set_xlim(self, limits):
        """set X limits"""
        if self._data_plot_widget:
            self._data_plot_widget.set_xlim(limits)
        else:
            qWarning("No plot widget; can't set X limits")

    def get_ylim(self):
        """get Y limits"""
        if self._data_plot_widget:
            return self._data_plot_widget.get_ylim()
        else:
            qWarning("No plot widget; returning default Y limits")
            return [0.0, 10.0]

    def set_ylim(self, limits):
        """set Y limits"""
        if self._data_plot_widget:
            self._data_plot_widget.set_ylim(limits)
        else:
            qWarning("No plot widget; can't set Y limits")

    # signal on y limit changed?
