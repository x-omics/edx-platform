.. _Working with Content Experiments:

#################################
Working with Content Experiments
#################################

This chapter describes how you can use content experiments in your course. See:

* :ref:`Overview of Content Experiments`
* :ref:`Enable Content Experiments`
* :ref:`Specify Group Configurations`
* :ref:`Configure the Content Experiment in Studio`
* :ref:`View a Content Experiment as Course Staff`
* :ref:`Configure the Content Experiment in XML`

.. _Overview of Content Experiments:

***********************************
Overview of Content Experiments
***********************************

With content experiments, you can:

* Create experiments that show different course content to different groups of
  students.

* Research and compare the performance of students in groups to gain more
  insight into the relative effectiveness of your course content.

* Specify the components that are in each group.

* Run multiple experiments in your course, each with any number of groups.

.. note::
  Students are randomly assigned to groups. You cannot control which students
  are assigned to which group.

.. _Enable Content Experiments:

****************************************
Enable Content Experiments
****************************************

To enable content experiments in your course, you add ``split_test`` to the
``advanced_modules`` policy key.

#. From the **Settings** menu, select **Advanced Settings**.
#. In the Advanced Settings page, find the ``advanced_modules`` policy key.
#. In the Policy Value field, add ``split_test``. 
#. At the bottom of the page, click **Save Changes**.

.. _Specify Group Configurations:

****************************************
Specify Group Configurations
****************************************

Before you can create content experiments, you must define group configurations.

A group configuration defines how many groups of students are in
an experiment. You can have any number of group configurations in your course.
When you create a content experiment, you select the group configuration to use.

For example, you may want to do one content experiment that presents either a
video or a reading assignment to your students, and another content experiment
that presents the same question using four different problem types. For the
first content experiment, you need a group configuration that divides your
students into two groups, and for the second content experiment, you need a
group configuration that divides your students into four groups.

For each of the group configurations that you define, students are assigned to
one of the possible groups. Students remain in those assigned groups regardless
of how many content experiments you set up that use the same group
configuration.

To specify group configurations, you modify the value for the
``user_partitions`` policy key in the Advanced Settings.

The value for ``user_partitions`` is a JSON collection of group configurations,
each of which defines the groups of students. See the following examples for
more information.

=============================================
Example:  One Group Configuration
=============================================

The following is an example JSON object that defines an group configuration with two student segments.

.. code-block:: json

    "user_partitions": [{"id": 0,
                       "name": "Name of the Group Configuration",
                       "description": "Description of the group configuration.",
                       "version": 1,
                       "groups": [{"id": 0,
                                   "name": "Segment A",
                                   "version": 1},
                                  {"id": 1,
                                   "name": "Segment B",
                                   "version": 1}]}]

In this example:

* The ``"id": 0`` identifies the group configuration. For XML courses, the value
  is referenced in the ``user_partition`` attribute of the ``<split_test>``
  element in the content experiment file.

* The ``groups`` array identifies the groups, or segments, to which
  students are randomly assigned. For XML courses, each group ``id`` value is
  referenced in the ``group_id_to_child`` attribute of the ``<split_test>``
  element.

==========================================================
Example: Multiple Group Configurations
==========================================================

The following is an example JSON object that defines two group configurations.
The first group configuration divides students into two groups, and the second
divides students into three groups.

.. code-block:: json

    "user_partitions": [{"id": 0,
                         "name": "Name of Group Configuration 1",
                         "description": "Description of Group Configuration 1.",
                         "version": 1,
                         "groups": [{"id": 0,
                                     "name": "Segment A",
                                     "version": 1},
                                    {"id": 1,
                                     "name": "Segment B",
                                     "version": 1}]}
                        {"id": 1,
                         "name": "Name of Group Configuration 2",
                         "description": "Description of Group Configuration 2.",
                         "version": 1,
                         "groups": [{"id": 2,
                                     "name": "Segment C",
                                     "version": 1},
                                    {"id": 3,
                                     "name": "Segment D",
                                     "version": 1}
                                     {"id": 4,
                                     "name": "Segment E",
                                     "version": 1}]}]

==========================================================
Modifying Group Configurations
==========================================================

After the course starts, **do not**:

* Delete group configurations.

* Change the ``id`` value of a group configuration or group.
  
You can add group configurations at any time.

==========================================================
Removing Groups from Group Configurations
==========================================================

After a course has started, you may find that students in a specific group are
having a problem or a poor experience. In this situation, you can remove the
group from the group configuration. Content that was specified for that
group is then no longer part of the course.

Students in the removed group are reassigned to one of the other groups in the
group configuration. Any problems that these students completed in the removed
group content do not count toward the students' grades. The students must begin
the problem set again and complete all the problems in the group content to
which they've been reassigned.

Removing a group impacts the course event data. Ensure that researchers
evaluating your course results are aware of the group you removed and the
date.

.. warning:: 
  Do not change the ``id`` value of groups after a course starts.

==============================================
Specify Group Configurations in an XML Course 
==============================================

If you are developing your course in XML, you define group configurations in the
``policy.json`` file in the ``policies`` directory. Use the same guidelines
given above for the ``user_partitions`` policy key in Advanced Settings.

See `Define the Experiment Content in the Split Test File`_ for more
information on how the XML for the content experiment uses these settings.


.. _Configure the Content Experiment in Studio:

********************************************
Configure a Content Experiment in Studio
********************************************

To configure a content experiment in Studio, you:

#. `Create the content experiment`_.
#. `Create content for groups in the content experiment`_.
   

================================
Create the Content Experiment
================================

After you :ref:`Enable Content Experiments` and :ref:`Specify Group
Configurations`, you can add content experiments to a unit page in the course
outline.

#. In a private or draft unit page, under **Add New Component** click
   **Advanced**.

#. Select **Content Experiment**.
   
   A new content experiment is added to the unit:

   .. image:: ../Images/content_experiment_block.png
    :alt: The content experiment component in a unit page

   You can work with the content experiment as you can any other component.  See
   :ref:`Components` for more information.

#. Click **Select a Group Configuration** or **Edit** to open the content
   experiment component.

   .. image:: ../Images/content_experiment_editor.png
    :alt: The content experiment editor

#. Select a group configuration.
   
   .. note:: 
     After you select a group configuration and save the content experiment, you
     cannot change the group configuration.

#. Modify the the **Display Name**.  The Display Name is only used in
   Studio; students do not see this value.

#. Click **Save**.

The content experiment is displayed in a unit page as a component that contains
other components. See :ref:`Components that Contain Other Components` for more
information.

You can now create content for the groups in the experiment.

================================================================
Create Content for Groups in the Content Experiment
================================================================
   
After you select a group configuration, in the content experiment component
click **View**.

The content experiment page that opens automatically includes a container for
each group that is defined in the group configuration you selected. For example,
if you select a group configuration that defines Group A and Group B, you see
the following page:

.. image:: ../Images/content_experiment_container.png
 :alt: The content experiment page with two groups

You add content to both groups as needed, just as you would add content to any
container page. See :ref:`Components that Contain Other Components` for more
information.

For example, you can add an HTML component and a video to Group A:

.. image:: ../Images/a_b_test_child_expanded.png
 :alt: Image of an expanded A/B test component


.. _View a Content Experiment as Course Staff:

*********************************************
View a Content Experiment as Course Staff
*********************************************

When you view a unit that contains a content experiment in the LMS in the Staff
view, you use a drop-down list to select a group. The unit page then shows the
content for that group of students.

For example, in the following page, Group 0 is selected, and the HTML component
and video that is part of Group 0 is displayed:

.. image:: ../Images/a-b-test-lms-group-0.png
 :alt: Image of a unit page with Group 0 selected

You can change the group selection to view the problem that a different group of
students sees:

.. image:: ../Images/a-b-test-lms-group-2.png
 :alt: Image of a unit page with Group 1 selected

.. note:: 
  The example course content in this chapter uses content experiment terminology
  to make the functionality clear. Typically, you would not use terminology in
  course content that would make students aware of the experiment.


.. _Configure the Content Experiment in XML:

****************************************
Configure the Content Experiments in XML
****************************************

You work with multiple XML files to configure a content experiment in your
course. This section steps through the files involved in a content experiment
that shows different content to two different groups of students.

For more information about working with your course's XML files, including
information about terminology, see the `edX XML Tutorial <http://edx.readthedocs
.org/projects/devdata/en/latest/course_data_formats/course_xml.html>`_.

=====================================================
Define the Content Experiment in the Sequential File
=====================================================

You reference a content experiment in the file for the subsection in the ``sequential`` directory. For example:

.. code-block:: xml

    ...
    <vertical url_name="name for the unit that contains the A/B test" display_name="A/B Test Unit">
        <split_test url_name="name of A/B test file in the split_test folder"/>
    </vertical>
    .....

The ``<split_test>`` element's ``url_name`` value references the name of the A/B test file in the ``split_test`` directory.


.. _Define the Experiment Content in the Split Test File:

=====================================================
Define the Experiment Content in the Split Test File
=====================================================

After you define the content experiment in the sequential file, you define the
course content you want to test in the file in the ``split_test`` directory.
This is the file referenced in the ``<split_test>`` element in the sequential
file, as shown above.

In the content experiment file, you add elements for the experiment content. For
this example, you add two `<vertical>`` elements to compare the two different
sets of content.

.. code-block:: xml

    <split_test url_name="AB_Test.xml" display_name="A/B Test" user_partition_id="0" 
                group_id_to_child='{"0": "i4x://path-to-course/vertical/group_a", 
                                    "1": "i4x://path-to-course/vertical/group_b"}'>
        <vertical url_name="group_a" display_name="Group A">
           <html>Welcome to group A.</html>
           <video url_name="group_a_video"/>
        </vertical>
        <vertical url_name="group_b" display_name="Group B">
            <html>Welcome to group B.</html>
            <problem display_name="Checkboxes">
                <p>A checkboxes problem presents checkbox buttons for student input. 
                   Students can select more than one option presented.</p>
                <choiceresponse>
                    <checkboxgroup direction="vertical" label="Select the answer that matches">
                        <choice correct="true">correct</choice>
                        <choice correct="false">incorrect</choice>
                        <choice correct="true">correct</choice>
                    </checkboxgroup>
                </choiceresponse>
            </problem>
        </vertical>
    </split_test>


In this example:

* The ``user_partition_id`` value references the ID of the experiment defined in
  the ``policy.json`` file.

* The ``group_id_to_child`` value references the IDs of the groups defined in
  the ``policy.json`` file, and maps the group IDs to specific content.

  For example,  the value for group ``0``, ``i4x://path-to-
  course/vertical/group_a``, maps to the ``<vertical>`` element with the
  ``url_name`` equal to ``group_a``.  Therefore, students in group 0 see the
  content in that vertical.

For information about the ``policy.json`` file, see :ref:`Specify Group
Configurations`.