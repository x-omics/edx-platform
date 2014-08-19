/**
 * Provides useful utilities for views.
 */
define(["jquery", "underscore", "gettext", "js/views/feedback_notification", "js/views/feedback_prompt"],
    function ($, _, gettext, NotificationView, PromptView) {
        var toggleExpandCollapse, showLoadingIndicator, hideLoadingIndicator, confirmThenRunOperation,
            runOperationShowingMessage, disableElementWhileRunning, getScrollOffset, setScrollOffset,
            setScrollTop, redirect, hasChangedAttributes;

        /**
         * Toggles the expanded state of the current element.
         */
        toggleExpandCollapse = function(target, collapsedClass) {
            // Support the old 'collapsed' option until fully switched over to is-collapsed
            if (!collapsedClass) {
                collapsedClass = 'collapsed';
            }
            target.closest('.expand-collapse').toggleClass('expand collapse');
            target.closest('.is-collapsible, .window').toggleClass(collapsedClass);
            target.closest('.is-collapsible').children('article').slideToggle();
        };

        /**
         * Show the page's loading indicator.
         */
        showLoadingIndicator = function() {
            $('.ui-loading').show();
        };

        /**
         * Hide the page's loading indicator.
         */
        hideLoadingIndicator = function() {
            $('.ui-loading').hide();
        };

        /**
         * Confirms with the user whether to run an operation or not, and then runs it if desired.
         */
        confirmThenRunOperation = function(title, message, actionLabel, operation, onCancelCallback) {
            return new PromptView.Warning({
                title: title,
                message: message,
                actions: {
                    primary: {
                        text: actionLabel,
                        click: function(prompt) {
                            prompt.hide();
                            operation();
                        }
                    },
                    secondary: {
                        text: gettext('Cancel'),
                        click: function(prompt) {
                            if (onCancelCallback) {
                                onCancelCallback();
                            }
                            return prompt.hide();
                        }
                    }
                }
            }).show();
        };

        /**
         * Shows a progress message for the duration of an asynchronous operation.
         * Note: this does not remove the notification upon failure because an error
         * will be shown that shouldn't be removed.
         * @param message The message to show.
         * @param operation A function that returns a promise representing the operation.
         */
        runOperationShowingMessage = function(message, operation) {
            var notificationView;
            notificationView = new NotificationView.Mini({
                title: gettext(message)
            });
            notificationView.show();
            return operation().done(function() {
                notificationView.hide();
            });
        };

        /**
         * Disables a given element when a given operation is running.
         * @param {jQuery} element the element to be disabled.
         * @param operation the operation during whose duration the
         * element should be disabled. The operation should return
         * a JQuery promise.
         */
        disableElementWhileRunning = function(element, operation) {
            element.addClass("is-disabled");
            return operation().always(function() {
                element.removeClass("is-disabled");
            });
        };

        /**
         * Performs an animated scroll so that the window has the specified scroll top.
         * @param scrollTop The desired scroll top for the window.
         */
        setScrollTop = function(scrollTop) {
            $('html, body').animate({
                scrollTop: scrollTop
            }, 500);
        };

        /**
         * Returns the relative position that the element is scrolled from the top of the view port.
         * @param element The element in question.
         */
        getScrollOffset = function(element) {
            var elementTop = element.offset().top;
            return elementTop - $(window).scrollTop();
        };

        /**
         * Scrolls the window so that the element is scrolled down to the specified relative position
         * from the top of the view port.
         * @param element The element in question.
         * @param offset The amount by which the element should be scrolled from the top of the view port.
         */
        setScrollOffset = function(element, offset) {
            var elementTop = element.offset().top,
                newScrollTop = elementTop - offset;
            setScrollTop(newScrollTop);
        };

        /**
         * Redirects to the specified URL. This is broken out as its own function for unit testing.
         */
        redirect = function(url) {
            window.location = url;
        };

        /**
         * Returns true if a model has changes to at least one of the specified attributes.
         * @param model The model in question.
         * @param attributes The list of attributes to be compared.
         * @returns {boolean} Returns true if attribute changes are found.
         */
        hasChangedAttributes = function(model, attributes) {
            var i, changedAttributes = model.changedAttributes();
            if (!changedAttributes) {
                return false;
            }
            for (i=0; i < attributes.length; i++) {
                if (_.has(changedAttributes, attributes[i])) {
                    return true;
                }
            }
            return false;
        };

        return {
            'toggleExpandCollapse': toggleExpandCollapse,
            'showLoadingIndicator': showLoadingIndicator,
            'hideLoadingIndicator': hideLoadingIndicator,
            'confirmThenRunOperation': confirmThenRunOperation,
            'runOperationShowingMessage': runOperationShowingMessage,
            'disableElementWhileRunning': disableElementWhileRunning,
            'setScrollTop': setScrollTop,
            'getScrollOffset': getScrollOffset,
            'setScrollOffset': setScrollOffset,
            'redirect': redirect,
            'hasChangedAttributes': hasChangedAttributes
        };
    });
