/**
 * The EditSectionXBlockModal is a Backbone view that shows an editor in a modal window.
 * It has nested views: for release date, due date, grading format, and staff lock.
 * It is invoked using the editXBlock method and uses xblock_info as a model,
 * and upon save parent invokes refresh function that fetches updated model and
 * re-renders edited course outline.
 */
define(['jquery', 'backbone', 'underscore', 'gettext', 'js/views/modals/base_modal',
    'date', 'js/views/utils/xblock_utils', 'js/utils/date_utils', 'js/views/utils/view_utils'
],
    function(
        $, Backbone, _, gettext, BaseModal, date, XBlockViewUtils, DateUtils, ViewUtils
    ) {
        'use strict';
        var EditSectionXBlockModal, BaseDateView, ReleaseDateView, DueDateView,
            GradingView, StaffLockView;

        EditSectionXBlockModal = BaseModal.extend({
            events : {
                'click .action-save': 'save',
                'click .action-modes a': 'changeMode'
            },

            options: $.extend({}, BaseModal.prototype.options, {
                modalName: 'edit-outline-item',
                modalType: 'edit-settings',
                addSaveButton: true,
                modalSize: 'med',
                viewSpecificClasses: 'confirm'
            }),

            initialize: function() {
                BaseModal.prototype.initialize.call(this);
                this.events = _.extend({}, BaseModal.prototype.events, this.events);
                this.template = this.loadTemplate('edit-outline-item-modal');
                this.options.title = this.getTitle();
                this.initializeComponents();
            },

            getTitle: function () {
                return _.template(
                    gettext('<%= sectionName %> Settings'),
                    { sectionName: this.model.get('display_name') }
                );
            },

            getContentHtml: function() {
                return this.template(this.getContext());
            },

            afterRender: function() {
                BaseModal.prototype.render.apply(this, arguments);
                this.invokeComponentMethod('afterRender');
            },

            save: function(event) {
                event.preventDefault();
                var requestData = _.extend({}, this.getRequestData(), {
                    metadata: this.getMetadata()
                });
                // Only update if something changed to prevent items from erroneously entering draft state
                if (!_.isEqual(requestData, { metadata: {} })) {
                    XBlockViewUtils.updateXBlockFields(this.model, requestData, {
                        success: this.options.onSave
                    });
                }
                this.hide();
            },

            /**
             * Call the method on each value in the list. If the element of the
             * list doesn't have such a method it will be skipped.
             * @param {String} methodName The method name needs to be called.
             * @return {Object}
             */
            invokeComponentMethod: function (methodName) {
                var values = _.map(this.components, function (component) {
                    if (_.isFunction(component[methodName])) {
                        return component[methodName].call(component);
                    }
                });

                return _.extend.apply(this, [{}].concat(values));
            },

            /**
             * Return context for the modal.
             * @return {Object}
             */
            getContext: function () {
                return _.extend({
                    xblockInfo: this.model,
                    xblockType: XBlockViewUtils.getXBlockType(this.model.get('category'), this.parentInfo, true)
                }, this.invokeComponentMethod('getContext'));
            },

            /**
             * Return request data.
             * @return {Object}
             */
            getRequestData: function () {
                return this.invokeComponentMethod('getRequestData');
            },

            /**
             * Return metadata for the XBlock.
             * @return {Object}
             */
            getMetadata: function () {
                return this.invokeComponentMethod('getMetadata');
            },

            /**
             * Initialize internal components.
             */
            initializeComponents: function () {
                this.components = [];
                this.components.push(
                    new StaffLockView({
                        selector: '.edit-staff-lock',
                        parentView: this,
                        model: this.model
                    })
                );

                if (this.model.isChapter() || this.model.isSequential()) {
                    this.components.push(
                        new ReleaseDateView({
                            selector: '.scheduled-date-input',
                            parentView: this,
                            model: this.model
                        })
                    );
                }

                if (this.model.isSequential()) {
                    this.components.push(
                        new DueDateView({
                            selector: '.due-date-input',
                            parentView: this,
                            model: this.model
                        }),
                        new GradingView({
                            selector: '.edit-settings-grading',
                            parentView: this,
                            model: this.model
                        })
                    );
                }
            }
        });

        BaseDateView = Backbone.View.extend({
            // Attribute name in the model, should be defined in children classes.
            fieldName: null,

            events : {
                'click .clear-date': 'clearValue'
            },

            afterRender: function () {
                this.setElement(this.options.parentView.$(this.options.selector).get(0));
                this.$('input.date').datepicker({'dateFormat': 'm/d/yy'});
                this.$('input.time').timepicker({
                    'timeFormat' : 'H:i',
                    'forceRoundTime': true
                });
                if (this.model.get(this.fieldName)) {
                    DateUtils.setDate(
                        this.$('input.date'), this.$('input.time'),
                        this.model.get(this.fieldName)
                    );
                }
            }
        });

        DueDateView = BaseDateView.extend({
            fieldName: 'due',

            getValue: function () {
                return DateUtils.getDate(this.$('#due_date'), this.$('#due_time'));
            },

            clearValue: function (event) {
                event.preventDefault();
                this.$('#due_time, #due_date').val('');
            },

            getMetadata: function () {
                return {
                    'due': this.getValue()
                };
            }
        });

        ReleaseDateView = BaseDateView.extend({
            fieldName: 'start',
            startingReleaseDate: null,

            afterRender: function () {
                BaseDateView.prototype.afterRender.call(this);
                // Store the starting date and time so that we can determine if the user
                // actually changed it when "Save" is pressed.
                this.startingReleaseDate = this.getValue();
            },

            getValue: function () {
                return DateUtils.getDate(this.$('#start_date'), this.$('#start_time'));
            },

            clearValue: function (event) {
                event.preventDefault();
                this.$('#start_time, #start_date').val('');
            },

            getMetadata: function () {
                var newReleaseDate = this.getValue();
                if (JSON.stringify(newReleaseDate) === JSON.stringify(this.startingReleaseDate)) {
                    return {};
                }
                return {
                    'start': newReleaseDate
                };
            }
        });

        GradingView = Backbone.View.extend({
            afterRender: function () {
                this.setElement(this.options.parentView.$(this.options.selector).get(0));
                this.setValue(this.model.get('format'));
            },

            setValue: function (value) {
                this.$('#grading_type').val(value);
            },

            getValue: function () {
                return this.$('#grading_type').val();
            },

            getRequestData: function () {
                return {
                    'graderType': this.getValue()
                };
            },

            getContext: function () {
                return {
                    graderTypes: JSON.parse(this.model.get('course_graders'))
                };
            }
        });

        StaffLockView = Backbone.View.extend({
            isModelLocked: function() {
                return this.model.get('has_explicit_staff_lock');
            },

            isAncestorLocked: function() {
                return this.model.get('ancestor_has_staff_lock');
            },

            afterRender: function () {
                this.setElement(this.options.parentView.$(this.options.selector).get(0));
                this.setLock(this.isModelLocked());
            },

            setLock: function(value) {
                this.$('#staff_lock').prop('checked', value);
            },

            isLocked: function() {
                return this.$('#staff_lock').is(':checked');
            },

            hasChanges: function() {
                return this.isModelLocked() != this.isLocked();
            },

            getRequestData: function() {
                return this.hasChanges() ? { publish: 'republish' } : {};
            },

            getMetadata: function() {
                // Setting visible_to_staff_only to null when disabled will delete the field from this
                // xblock, allowing it to inherit the value of its ancestors.
                return this.hasChanges() ? { visible_to_staff_only: this.isLocked() ? true : null } : {};
            },

            getContext: function () {
                return {
                    hasExplicitStaffLock: this.isModelLocked(),
                    ancestorLocked: this.isAncestorLocked()
                }
            }
        });

        return EditSectionXBlockModal;
    });
